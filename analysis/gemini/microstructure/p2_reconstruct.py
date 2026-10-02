"""P2: reconstruct the observable book state immediately before every trade.

INPUT  raw hi-fi tape from lane_recorder.py (every @depth diff, every @trade,
       periodic @depth20 snapshots). Nothing was sampled down.
OUTPUT one record per trade with the pre-trade book, the queue ahead under
       EXPLICIT assumptions, the following book update, and a cause label for
       any displayed size that disappeared.

BOOK RECONSTRUCTION
-------------------
Seed from a `depth_snapshot` (carries `lastUpdateId`). Then apply each
`depth_update` diff. Diffs chain: event N's `u` equals event N+1's `U`. That
chain is CHECKED, not assumed -- a break marks the book DESYNCED and every
trade in a desynced span is labelled UNKNOWN rather than scored. A level with
size "0.00" is removed.

ORDERING, AND ITS LIMIT -- STATED, NOT BURIED
---------------------------------------------
`depth_update` carries venue sequence ids (`U`,`u`) AND a venue event time
(`E`, nanoseconds). Trades carry `E` and a trade id, but NO sequence id. So
trade-vs-book ordering rests on venue event time alone. Where a trade's `E`
ties a book event's `E`, or the book delta cannot be reconciled with the
trade, the case is labelled **UNKNOWN**. We do not invent ordering the venue
does not establish.

DISAPPEARANCE CAUSE at the execution price, from the next diff touching it:
  TRADE            size fell by exactly the traded qty
  TRADE_PLUS_CANCEL size fell by more than the traded qty
  TRADE_PARTIAL    size fell by less than the traded qty (refresh/add mixed in)
  CANCEL_OR_REPRICE size fell with no trade to account for it
  REPLENISHED      size rose
  UNCHANGED        size identical
  UNKNOWN          desynced, no following diff, or ordering not establishable

Queue ahead is reported under two EXPLICIT, separately-labelled assumptions:
  q_ahead_displayed  the size displayed at our level before the trade. This
                     is a PROXY: hidden or refreshing queue is unobservable.
  q_ahead_zero       0, the front-of-queue ceiling. Unreachable by
                     construction; reported only as a bound.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def D(x):
    return None if x is None else float(x)


def load(tape_glob):
    """Group every raw row by market, preserving arrival order."""
    by_market = defaultdict(list)
    windows = {}
    vols = defaultdict(list)
    files = sorted(Path(".").glob(tape_glob)) if isinstance(tape_glob, str) else tape_glob
    for f in files:
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "window":
                windows[r["market"]] = r
            elif k == "vol":
                vols[r["window"]].append(r)
            elif k in ("depth_update", "depth_snapshot", "trade"):
                m = r.get("market")
                if m:
                    by_market[m].append(r)
    return by_market, windows, vols


def apply_diff(book, side_rows):
    for px, sz in side_rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s


def best(book, is_bid):
    if not book:
        return None, None
    p = max(book) if is_bid else min(book)
    return p, book[p]


def reconstruct(rows, meta):
    """Walk one market's event stream. Returns per-trade records."""
    bids, asks = {}, {}
    seeded = False
    desync = True
    last_u = None
    last_book_t = None     # local recv time of last book event
    last_book_E = None     # venue event time (ns) of last book event
    out = []
    pending = []           # trades awaiting the next diff at their price

    for i, r in enumerate(rows):
        k = r["kind"]
        if k == "depth_snapshot":
            bids, asks = {}, {}
            apply_diff(bids, r.get("bids"))
            apply_diff(asks, r.get("asks"))
            last_u = r.get("lastUpdateId")
            seeded, desync = True, False
            last_book_t, last_book_E = r.get("t"), None
            continue

        if k == "depth_update":
            U, u = r.get("U"), r.get("u")
            if not seeded:
                continue
            if last_u is not None and U is not None and U != last_u:
                # chain broken: the book is no longer trustworthy
                desync = True
            # resolve any trade waiting for the next diff at its price
            for pend in pending:
                lvl = (r.get("b") if pend["side_book"] == "bid" else r.get("a")) or []
                hit = [s for px, s in lvl if float(px) == pend["price"]]
                if hit:
                    after = float(hit[0])
                    befor = pend["displayed_at_price"]
                    pend["size_after"] = after
                    if befor is None:
                        pend["disappearance_cause"] = "UNKNOWN"
                    else:
                        drop = befor - after
                        q = pend["qty"]
                        if after > befor:
                            pend["disappearance_cause"] = "REPLENISHED"
                        elif drop == 0:
                            pend["disappearance_cause"] = "UNCHANGED"
                        elif abs(drop - q) < 1e-9:
                            pend["disappearance_cause"] = "TRADE"
                        elif drop > q:
                            pend["disappearance_cause"] = "TRADE_PLUS_CANCEL"
                        else:
                            pend["disappearance_cause"] = "TRADE_PARTIAL"
                    pend["next_book_E_ns"] = r.get("E_ns")
                    pend["next_book_dt_s"] = (
                        None if pend["t"] is None or r.get("t") is None
                        else round(r["t"] - pend["t"], 4))
                    out.append(pend)
            pending = [p for p in pending if "disappearance_cause" not in p]

            apply_diff(bids, r.get("b"))
            apply_diff(asks, r.get("a"))
            last_u = u
            if U is not None and last_u is not None:
                desync = desync and False if U == r.get("U") else desync
            last_book_t, last_book_E = r.get("t"), r.get("E_ns")
            continue

        if k != "trade":
            continue

        px, qty = D(r.get("price")), D(r.get("qty"))
        bim = r.get("buyer_is_maker")
        bb, bbsz = best(bids, True)
        ba, basz = best(asks, False)
        # buyer_is_maker names the PASSIVE side:
        #   True  -> buyer was maker -> taker SOLD into the BID
        #   False -> seller was maker -> taker BOUGHT from the ASK
        side_book = "bid" if bim is True else ("ask" if bim is False else None)
        disp = None
        if side_book == "bid":
            disp = bids.get(px)
        elif side_book == "ask":
            disp = asks.get(px)

        if side_book == "bid":
            cls = ("AT_BID" if (bb is not None and px == bb)
                   else "BELOW_BID" if (bb is not None and px < bb)
                   else "INSIDE_OR_ABOVE_BID" if bb is not None else "NO_BID")
        elif side_book == "ask":
            cls = ("AT_ASK" if (ba is not None and px == ba)
                   else "ABOVE_ASK" if (ba is not None and px > ba)
                   else "INSIDE_OR_BELOW_ASK" if ba is not None else "NO_ASK")
        else:
            cls = "UNKNOWN_SIDE"

        rec = {
            "market": r.get("market"),
            "window": (meta or {}).get("window"),
            "expiry_t": (meta or {}).get("expiry_t"),
            "t": r.get("t"),
            "trade_E_ns": r.get("E_ns"),
            "trade_id": r.get("trade_id"),
            "price": px, "qty": qty, "buyer_is_maker": bim,
            "passive_side": side_book,
            "best_bid": bb, "best_bid_sz": bbsz,
            "best_ask": ba, "best_ask_sz": basz,
            "displayed_at_price": disp,
            "q_ahead_displayed": disp,
            "q_ahead_zero": 0.0,
            "price_class": cls,
            "book_desynced": bool(desync or not seeded),
            "last_book_E_ns": last_book_E,
            "elapsed_since_book_event_s": (
                None if (last_book_t is None or r.get("t") is None)
                else round(r["t"] - last_book_t, 4)),
            "ordering_basis": ("venue_event_time_ns"
                               if (last_book_E and r.get("E_ns")) else "UNKNOWN"),
            "ordering_tie": bool(last_book_E and r.get("E_ns")
                                 and last_book_E == r.get("E_ns")),
            "side_book": side_book,
        }
        if rec["book_desynced"] or side_book is None:
            rec["disappearance_cause"] = "UNKNOWN"
            out.append(rec)
        else:
            pending.append(rec)

    for p in pending:                      # no following diff ever arrived
        p["disappearance_cause"] = "UNKNOWN"
        p["size_after"] = None
        out.append(p)
    return out


def main():
    glob = sys.argv[1] if len(sys.argv) > 1 else "data/gemini_hifi/hifi_*.jsonl"
    by_market, windows, vols = load(glob)
    allrecs = []
    for m, rows in by_market.items():
        allrecs += reconstruct(rows, windows.get(m))
    allrecs.sort(key=lambda r: (r["t"] or 0))

    out = Path("analysis/gemini/microstructure/p2_pretrade_book.json")
    out.write_text(json.dumps(allrecs, indent=1))

    import collections
    print(f"markets with events : {len(by_market)}")
    print(f"trades reconstructed: {len(allrecs)}")
    if not allrecs:
        print("no trades yet"); return
    btc = [r for r in allrecs if "BTC05M" in (r["market"] or "")]
    print(f"  of which BTC05M   : {len(btc)}")
    print()
    for field in ("price_class", "disappearance_cause", "book_desynced",
                  "ordering_basis", "ordering_tie"):
        c = collections.Counter(str(r.get(field)) for r in btc)
        print(f"{field}:")
        for k, v in c.most_common():
            print(f"    {k:22s} {v:5d}  ({v/len(btc)*100:.1f}%)")
    el = [r["elapsed_since_book_event_s"] for r in btc
          if r["elapsed_since_book_event_s"] is not None]
    if el:
        el.sort()
        print(f"\nelapsed since last book event (s): p50={el[len(el)//2]:.4f} "
              f"p90={el[int(.9*len(el))]:.4f} max={el[-1]:.4f}")
    qa = [r["q_ahead_displayed"] for r in btc if r["q_ahead_displayed"] is not None]
    if qa:
        qa.sort()
        print(f"q_ahead_displayed (contracts)    : p50={qa[len(qa)//2]} "
              f"p90={qa[int(.9*len(qa))]} max={qa[-1]}  n={len(qa)}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()

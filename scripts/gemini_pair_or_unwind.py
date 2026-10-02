#!/usr/bin/env python3
"""PAIR_OR_UNWIND: treat every defensible first maker fill as an independent lot
with two competing exits. Lot-based, not inventory-based.

A STRUCTURAL FACT THAT CHANGES WHAT "PAIR" MEANS ON THIS VENUE, established
from the data before anything was built: a Gemini BTC window lists exactly ONE
instrument (`...-UP`). There is no complement token. Both the canonical tape
(182,119 rows, suffix `UP` on every one) and the live events feed (1 contract
per window) agree.

So buying NO at q is the same trade as selling UP at s = 1 - q, and therefore

    pair cost = p1 + (1 - s)
    pair cost <= ceiling   <=>   s >= p1 + (1 - ceiling)
    profit on a completed pair = 1 - pair cost = s - p1

**PAIR and UNWIND are the SAME ACTION at different prices.** PAIR is an exit at
or above a profit threshold; UNWIND is an exit that may be at a loss. The
ceilings map exactly:

    ceiling 0.99 -> exit >= entry + 0.01      0.97 -> entry + 0.03
    ceiling 0.98 -> entry + 0.02              0.95 -> entry + 0.05

That is not a weakening of the hypothesis. It is the hypothesis, stated in the
only terms this venue can express, and it is still materially different from
persistent two-sided quoting: it never requires the market to hand us a second
maker fill on the opposite side of the book, because the exit IS the opposite
side of the same book.

A lot can hold only ONE resting order at a time, so the policy is explicit:
rest at the PAIR price while inside the risk budget; cross to the touch when
the taker threshold trips or the deadline arrives. Once exited, the lot is
permanently closed and can never be paired.

THRESHOLDS ARE PRE-REGISTERED, NOT FITTED. The taker loss thresholds are
0.02 and 0.05, chosen to mirror the pair ceilings before any result was seen.
Nothing is tuned on the 75-fill toxicity analysis.

RULES CARRIED OVER UNCHANGED: fills only on defensible trade-through; only
trades reduce queue-ahead; aggressor side decides the consumed side;
unknown-side trades fill nothing; no fill is inferred from the touch; no queue
priority is invented; settlement is never imputed.
"""
from __future__ import annotations
import json, os, sys, collections, statistics
from decimal import Decimal as D
from datetime import datetime, timezone

sys.path.insert(0, os.getcwd())
from scripts.gemini_twosided_sim import Book, rebate, TICK   # noqa: E402

OUT = "analysis/gemini/PAIR_OR_UNWIND.json"
CEILINGS = (D("0.99"), D("0.98"), D("0.97"), D("0.95"))
TAKER_LOSS = (None, D("0.02"), D("0.05"))     # None = passive unwind only
QS = (5, 10, 25)
DEADLINE_S = 90                                # risk deadline before expiry


def run(frames_by_sym, meta, sym, q, ceiling, taker_loss, mode):
    """mode: 'pair_or_unwind' | 'two_sided' | 'forced'"""
    seq = frames_by_sym[sym]
    exp = None
    m = meta.get(sym) or {}
    if m.get("event_expiry"):
        exp = datetime.fromisoformat(m["event_expiry"].replace("Z", "+00:00"))
    book = Book()
    entry = {"buy": None, "sell": None}     # resting ENTRY quotes (first leg)
    lots = []                               # open lots
    closed = []
    cash = D(0)
    fees = D(0)                             # maker fees are zero on this venue
    reb = D(0)
    cap_hours = D(0)
    last_ts = None

    for ts, f in seq:
        book.apply(f.get("changes") or [])
        bb, ba = book.bb(), book.ba()
        past_deadline = bool(exp and (exp - ts).total_seconds() <= DEADLINE_S)
        if last_ts is not None:
            dt = D(str((ts - last_ts).total_seconds())) / D(3600)
            cap_hours += dt * sum(l["px"] * l["open"] for l in lots)
        last_ts = ts

        for t in (f.get("trades") or []):
            try:
                tpx = D(str(t["price"])); tq = D(str(t.get("quantity") or t.get("amount")))
            except Exception:
                continue
            side = str(t.get("side") or "").lower()
            consumed = "buy" if side == "sell" else ("sell" if side == "buy" else None)
            if consumed is None:
                continue
            avail = tq

            # ---- exit legs first: an open lot's resting order has priority
            for lot in lots:
                if lot["open"] <= 0 or avail <= 0:
                    continue
                ex = lot["exit_side"]
                if ex != consumed:
                    continue
                epx, ahead, got = lot["rest"]
                if epx is None:        # no resting exit order placed yet
                    continue
                if consumed == "buy" and tpx > epx:
                    continue
                if consumed == "sell" and tpx < epx:
                    continue
                if ahead > 0:
                    eat = min(ahead, avail); ahead -= eat; avail -= eat
                if avail > 0 and lot["open"] > 0:
                    take = min(avail, lot["open"])
                    avail -= take
                    cash += (epx * take) if ex == "sell" else (-epx * take)
                    reb += rebate(take, epx)
                    lot["open"] -= take
                    lot["exit_px"] = epx
                    lot["exit_t"] = ts
                    lot["exit_kind"] = "PAIR_MAKER" if lot["pair_resting"] else "UNWIND_MAKER"
                lot["rest"] = (epx, ahead, got)

            # ---- entry leg (first lot), only while inside the window
            if avail <= 0 or past_deadline:
                continue
            st = entry.get(consumed)
            if not st:
                continue
            mypx, ahead, got = st
            if consumed == "buy" and tpx > mypx:
                continue
            if consumed == "sell" and tpx < mypx:
                continue
            if ahead > 0:
                eat = min(ahead, avail); ahead -= eat; avail -= eat
            if avail > 0 and got < q:
                take = min(avail, D(q) - got); got += take
                cash += (-mypx * take) if consumed == "buy" else (mypx * take)
                reb += rebate(take, mypx)
                lots.append({"px": mypx, "qty": take, "open": take,
                             "side": consumed, "t": ts,
                             "exit_side": "sell" if consumed == "buy" else "buy",
                             "rest": (None, D(0), D(0)), "pair_resting": True,
                             "exit_px": None, "exit_t": None, "exit_kind": None,
                             "first_pair_seen_t": None, "best_pair_cost": None})
            entry[consumed] = (mypx, ahead, got) if got < q else None

        # ---- (re)place resting orders
        if bb is None or ba is None or ba <= bb:
            continue
        for lot in lots:
            if lot["open"] <= 0:
                continue
            need = (lot["px"] + (D(1) - ceiling)) if lot["side"] == "buy" \
                else (lot["px"] - (D(1) - ceiling))
            # observable pair cost right now, from the executable opposite touch
            # EXECUTABLE pair cost. Closing a LONG means SELLING, which
            # executes at the BID -- not the ask. An earlier revision used `ba`
            # here and so reported the pair one full spread better than it was;
            # on a 13c median spread that turned a median best pair cost of
            # ~0.87 into ~0.74 and made a qualifying complement look available
            # on 92.6% of lots. Wrong side of the book, same family as the
            # withdrawn summed-markout and the double-counted cleanup.
            now_cost = (lot["px"] + (D(1) - bb)) if lot["side"] == "buy" \
                else ((D(1) - lot["px"]) + ba)
            if lot["best_pair_cost"] is None or now_cost < lot["best_pair_cost"]:
                lot["best_pair_cost"] = now_cost
            if now_cost <= ceiling and lot["first_pair_seen_t"] is None:
                lot["first_pair_seen_t"] = ts
            # mark-to-market loss against the executable exit touch
            mtm = (bb - lot["px"]) if lot["side"] == "buy" else (lot["px"] - ba)
            trip = (taker_loss is not None and mtm <= -taker_loss)
            if mode == "two_sided":
                tgt, pr = (ba if lot["side"] == "buy" else bb), False
            elif past_deadline or trip or mode == "forced":
                # cross NOW, bounded by observed depth at the touch
                tpx_ = bb if lot["side"] == "buy" else ba
                depth = book.size_at("buy" if lot["side"] == "buy" else "sell", tpx_)
                take = min(lot["open"], depth)
                if take > 0:
                    cash += (tpx_ * take) if lot["side"] == "buy" else (-tpx_ * take)
                    lot["open"] -= take
                    lot["exit_px"] = tpx_; lot["exit_t"] = ts
                    lot["exit_kind"] = "UNWIND_TAKER" if trip and not past_deadline \
                        else ("FORCED" if mode == "forced" else "DEADLINE_TAKER")
                continue
            else:
                tgt, pr = (need if lot["side"] == "buy" else need), True
                tgt = max(tgt, ba) if lot["side"] == "buy" else min(tgt, bb)
            lot["pair_resting"] = pr
            if lot["rest"][0] != tgt:
                lot["rest"] = (tgt, book.size_at(lot["exit_side"], tgt), D(0))
        if not past_deadline:
            for s_, px_ in (("buy", bb), ("sell", ba)):
                cur = entry.get(s_)
                if (cur is None or cur[0] != px_) and not lots:
                    entry[s_] = (px_, book.size_at(s_, px_), D(0))

    for lot in lots:
        closed.append(lot)
    return {"symbol": sym, "q": q, "ceiling": str(ceiling), "mode": mode,
            "taker_loss": str(taker_loss) if taker_loss else "none",
            "lots": len(closed),
            "lots_unresolved": sum(1 for l in closed if l["open"] > 0),
            "paired": sum(1 for l in closed if l["exit_kind"] == "PAIR_MAKER"),
            "unwound_maker": sum(1 for l in closed if l["exit_kind"] == "UNWIND_MAKER"),
            "unwound_taker": sum(1 for l in closed if l["exit_kind"] == "UNWIND_TAKER"),
            "deadline_taker": sum(1 for l in closed if l["exit_kind"] == "DEADLINE_TAKER"),
            "forced": sum(1 for l in closed if l["exit_kind"] == "FORCED"),
            "net_before_comp": str(cash), "modeled_rebate": str(reb),
            "net_after_comp": str(cash + reb), "fees": str(fees),
            "capital_hours": str(cap_hours),
            "time_to_pair_s": [ (l["first_pair_seen_t"]-l["t"]).total_seconds()
                                for l in closed if l["first_pair_seen_t"] ],
            "time_to_exit_s": [ (l["exit_t"]-l["t"]).total_seconds()
                                for l in closed if l["exit_t"] ],
            "best_pair_costs": [str(l["best_pair_cost"]) for l in closed
                                if l["best_pair_cost"] is not None],
            "lot_rows": [{"px": str(l["px"]), "qty": str(l["qty"]),
                          "side": l["side"], "exit_kind": l["exit_kind"],
                          "exit_px": str(l["exit_px"]) if l["exit_px"] else None,
                          "open_at_end": str(l["open"]),
                          "best_pair_cost": str(l["best_pair_cost"])
                          if l["best_pair_cost"] is not None else None}
                         for l in closed]}


def main(tape):
    frames = []
    meta = {}
    for line in open(tape):
        if not line.strip():
            continue
        r = json.loads(line)
        if r["kind"] == "symbols":
            meta.update(r.get("meta") or {})
        elif r["kind"] == "frame" and r["f"].get("type") == "l2_updates":
            frames.append((datetime.fromisoformat(r["t"]), r["f"]))
    by = collections.defaultdict(list)
    for ts, f in frames:
        if f.get("symbol") and "BTC" in f["symbol"]:
            by[f["symbol"]].append((ts, f))

    rows = []
    for sym in by:
        for q in QS:
            for ceil in CEILINGS:
                for tl in TAKER_LOSS:
                    rows.append(run(by, meta, sym, q, ceil, tl, "pair_or_unwind"))
            rows.append(run(by, meta, sym, q, D("0.99"), None, "two_sided"))
            rows.append(run(by, meta, sym, q, D("0.99"), None, "forced"))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"tape": tape, "deadline_s": DEADLINE_S,
               "structural_note": "one instrument per window: PAIR == profitable "
                                  "UNWIND; ceilings map to exit >= entry+(1-ceiling)",
               "rows": rows}, open(OUT, "w"), indent=2, default=str)
    print(f"BTC symbols {len(by)}  configurations {len(rows)}")
    return rows


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else "data/gemini_shadow/merged/canonical_5m_tape.jsonl")

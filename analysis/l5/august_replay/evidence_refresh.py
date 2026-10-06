"""How long does L5's passive interest plausibly stay live? Measured, not swept.

THE QUESTION THIS ANSWERS, AND THE ONE IT REFUSES. S1 stranded a bid at
0.3800 for 876 s while the touch ran to 0.999, because `order_lifetime_s` is
None and an order is replaced only after it fills. A repricing rule is
therefore implicated -- but "which timeout maximises P&L?" is not asked here
and cannot be answered by this module: it never computes a fill, a position
or a cash figure. It reads L5's OWN fills against the book and asks what the
evidence will support about staleness.

THE OBSERVABLE. For each L5 fill at price p on token T at time t, measure how
far behind the market that price already was:

  offset_ticks   (best_bid(t-1) - p) / 0.01     -- how deep below the touch
  staleness_s    t - (the last moment p was still AT OR ABOVE the best bid)

`staleness_s` is the quantity that bounds a refresh rule. It says: an order
resting at p had been *behind the touch* for this long when a fill arrived
there. If essentially no L5 fill ever lands at a price that has been behind
the touch for longer than S seconds, then an order left sitting beyond S has
no evidential basis for being live interest, and a passive-maintenance rule
may cancel and re-place it without contradicting anything L5 is observed to
do.

WHY A PERCENTILE AND NOT THE MAXIMUM. The maximum is one observation and is
set by the single most extreme row; the dataset's own accuracy bound is one
60 s resync, so a lone large value is as likely to be carry as behaviour. The
bound is read at p95 and p99 and BOTH are reported, with the max beside them,
so the choice is visible rather than buried.

DERIVED FROM THE DIAGNOSTIC WINDOW ONLY. The other 11 L5-active windows are
the generality test and are not read here. Deriving the bound from all 12
would leak the test set into the mechanism.

NOTHING HERE IS ATTRIBUTED TO L5'S ORDER. `best_bid` is a price level in a
public book. "p was at or above the best bid" says the price was at the front
of the market, not that the order there was his.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book, offset_ticks
from analysis.l5.august_replay.select_window import load_books

TICK = D("0.01")
#: how far back to look for the last moment our price was at/above the touch
LOOKBACK_S = 900.0


def staleness_s(bk: Book, price: Any, *, until: float,
                lookback_s: float = LOOKBACK_S) -> Optional[float]:
    """Seconds `price` had been STRICTLY BELOW the best bid, as of `until`.

    Walks backwards through the capture's own rows. Returns 0.0 if the price
    was still at or above the touch at `until` (not stale at all), and None
    if the question cannot be answered inside the lookback -- never a default.
    """
    if not bk.t:
        return None
    try:
        p = D(str(price))
    except Exception:                                          # noqa: BLE001
        return None
    i = bisect.bisect_right(bk.t, until) - 1
    if i < 0:
        return None
    # if p is at or above the touch right now, it is not behind the market
    bb = bk.rows[i].get("best_bid")
    if bb is not None and p >= D(str(bb)):
        return 0.0
    last_ok = None
    while i >= 0 and until - bk.t[i] <= lookback_s:
        bb = bk.rows[i].get("best_bid")
        if bb is not None and p >= D(str(bb)):
            last_ok = bk.t[i]
            break
        i -= 1
    if last_ok is None:
        return None            # never at the touch inside the lookback
    return round(until - last_ok, 1)


def q(vals: List[float], p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[int(p * (len(s) - 1))], 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times", required=True)
    ap.add_argument("--selection", required=True)
    ap.add_argument("--out",
                    default="analysis/l5/august_replay/EVIDENCE_REFRESH.json")
    a = ap.parse_args(argv)

    sel = json.load(open(a.selection))
    slug = sel["selected"]
    srow = next(r for r in sel["rows"] if r["market_slug"] == slug)
    start, end = srow["window_start_utc"], srow["window_end_utc"]
    books, _ = load_books(a.parquet, slugs={slug})
    bt = {int(k): v for k, v in
          json.load(open(a.block_times))["block_times"].items()}
    bf = next(w for w in json.load(open(a.backfill))["rows"]
              if w["market_slug"] == slug)

    rows: List[Dict[str, Any]] = []
    for f in bf["fills"]:
        ts = bt.get(f.get("block"))
        if ts is None or not (start <= ts < end):
            continue
        bk = books.get(f["token_id"])
        if bk is None:
            continue
        snap = bk.at(ts - 1, max_age_s=MAX_CARRY_S)
        rows.append({
            "t": ts, "token_id": f["token_id"], "price": f["price"],
            "size": f["size"],
            "best_bid_t_minus_1": (snap or {}).get("best_bid"),
            "best_ask_t_minus_1": (snap or {}).get("best_ask"),
            "offset_ticks": offset_ticks((snap or {}).get("best_bid"),
                                         f["price"]),
            "staleness_s": staleness_s(bk, f["price"], until=float(ts)),
        })

    # inter-fill cadence per token: the other observable the brief names
    cad: Dict[str, List[float]] = {}
    for tok in {r["token_id"] for r in rows}:
        ts = sorted(r["t"] for r in rows if r["token_id"] == tok)
        cad[tok] = [round(b - a_, 1) for a_, b in zip(ts, ts[1:])]
    all_cad = [x for v in cad.values() for x in v]

    st = [r["staleness_s"] for r in rows if r["staleness_s"] is not None]
    st_unres = len([r for r in rows if r["staleness_s"] is None])
    # the non-trivially-stale subset: fills that were actually behind the touch
    st_pos = [x for x in st if x > 0]
    off = [r["offset_ticks"] for r in rows if r["offset_ticks"] is not None]

    rep = {
        "purpose": ("bound how long L5's passive interest plausibly stays "
                    "live, from his own fills against the public book"),
        "derived_from_window_only": slug,
        "other_windows_not_read": True,
        "no_pnl_computed_here": True,
        "n_fills_in_window": len(rows),
        "staleness_s": {
            "what": ("seconds the fill price had been STRICTLY BELOW the best "
                     "bid when the fill arrived; 0 means it was still at or "
                     "above the touch"),
            "n_resolved": len(st), "n_unresolved": st_unres,
            "zero_i_e_at_or_above_touch": len(st) - len(st_pos),
            "share_at_or_above_touch": (round((len(st) - len(st_pos)) / len(st), 4)
                                        if st else None),
            "p50": q(st, 0.50), "p90": q(st, 0.90), "p95": q(st, 0.95),
            "p99": q(st, 0.99), "max": q(st, 1.0),
            "positive_only": {"n": len(st_pos), "p50": q(st_pos, 0.50),
                              "p90": q(st_pos, 0.90), "p95": q(st_pos, 0.95),
                              "p99": q(st_pos, 0.99), "max": q(st_pos, 1.0)},
        },
        "offset_ticks_below_touch_at_T_minus_1": {
            "n": len(off), "p50": q(off, 0.50), "p90": q(off, 0.90),
            "p95": q(off, 0.95), "p99": q(off, 0.99), "max": q(off, 1.0)},
        "inter_fill_cadence_s_per_token": {
            "n": len(all_cad), "p50": q(all_cad, 0.50), "p90": q(all_cad, 0.90),
            "p95": q(all_cad, 0.95), "p99": q(all_cad, 0.99),
            "max": q(all_cad, 1.0),
            "what": ("seconds between consecutive L5 fills on the SAME "
                     "token; a short cadence means his interest on that side "
                     "is being consumed and re-established continuously")},
        "accuracy_bound_s": MAX_CARRY_S,
        "labels": ("best_bid is a price level in a public book. 'the price "
                   "was at or above the best bid' says it was at the front "
                   "of the market, NOT that the order there was L5's."),
        "fills": rows,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in rep.items() if k != "fills"},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

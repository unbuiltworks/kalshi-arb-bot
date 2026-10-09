"""The 30 seconds before each L5 fill, one second at a time.

The existing offset table samples three leads (T-30, T-5, T-1) and reports a
distribution at each. That is enough to say L5 rests deep and the touch falls
to him; it is not enough to say HOW the touch arrives -- steadily over the
half minute, or in a jump in the last second or two. The difference decides
what a replication's quote has to survive, so this walks every second.

MEASURED PER FILL, at each lead 30..0:
  dist_ticks    (own best bid - fill price) in ticks: 0 means the fill price
                IS the touch, positive means the touch is above it (the fill
                price is deeper), negative means the fill price is above the
                bid and is not a resting bid at that lead.
  spread_ticks  own book's ask - bid.
And after the fill, at +5/+15/+30 s: the signed mid move, positive meaning
the market moved in the buyer's favour.

TIMESTAMP UNCERTAINTY. A fill is timed by its BLOCK's real timestamp, not
interpolated. Polygon blocks are ~2 s apart, so the fill's true moment is
inside its block's interval and the lead axis carries that much slack; the
block gap is reported per fill so the slack is visible rather than assumed
small. A book snapshot is carried forward at most MAX_CARRY_S and a lead with
no snapshot inside that bound is NOT counted at that lead.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from analysis.l5.historical_l2.bbo_offset import Book, placement_consistency

TICK = D("0.01")
LEADS = tuple(range(30, -1, -1))
AFTER = (5, 15, 30)


def _pct(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1)))], 3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times",
                    default="analysis/l5/august_replay/block_times.json")
    ap.add_argument("--out",
                    default="analysis/l5/historical_l2/resting_trajectory.json")
    a = ap.parse_args(argv)

    import pyarrow.parquet as pq
    d = pq.read_table(a.parquet).to_pydict()
    books: Dict[str, Book] = defaultdict(Book)
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        books[str(d["token_id"][i])].add(
            d["captured_at"][i].timestamp(),
            {"best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i],
             "bid_prices": d["bid_prices"][i], "bid_sizes": d["bid_sizes"][i]})
    for b in books.values():
        b.sort()
    bt = {int(k): float(v) for k, v in
          json.load(open(a.block_times))["block_times"].items()}
    bf = json.load(open(a.backfill))

    dist: Dict[int, List[float]] = defaultdict(list)
    spread: Dict[int, List[float]] = defaultdict(list)
    at_or_better: Dict[int, List[int]] = defaultdict(list)
    after: Dict[int, List[float]] = defaultdict(list)
    block_gaps: List[float] = []
    n = 0
    blocks = sorted(bt)
    nxt = {b: bt[blocks[i + 1]] for i, b in enumerate(blocks[:-1])}

    for w in bf["rows"]:
        for lg in w["fills"]:
            if lg.get("l5_role") != "maker":
                continue
            blk = int(lg.get("block") or 0)
            tok = str(lg["token_id"])
            if blk not in bt or tok not in books:
                continue
            t, px = bt[blk], D(str(lg["price"]))
            n += 1
            if blk in nxt:
                block_gaps.append(round(nxt[blk] - t, 3))
            for lead in LEADS:
                s = books[tok].at(t - lead)
                if not s or s.get("best_bid") is None:
                    continue
                bb = D(str(s["best_bid"]))
                dist[lead].append(float((bb - px) / TICK))
                if s.get("best_ask") is not None:
                    spread[lead].append(
                        float((D(str(s["best_ask"])) - bb) / TICK))
                at_or_better[lead].append(
                    1 if placement_consistency(s, lg["price"])
                    == "RESTING_BID_CONSISTENT" else 0)
            s0 = books[tok].at(t)
            if s0 and s0.get("best_bid") is not None and \
                    s0.get("best_ask") is not None:
                mid0 = (D(str(s0["best_bid"])) + D(str(s0["best_ask"]))) / 2
                for k in AFTER:
                    s1 = books[tok].at(t + k)
                    if not s1 or s1.get("best_bid") is None or \
                            s1.get("best_ask") is None:
                        continue
                    mid1 = (D(str(s1["best_bid"]))
                            + D(str(s1["best_ask"]))) / 2
                    after[k].append(float(mid1 - mid0))

    traj = {}
    for lead in LEADS:
        xs = dist[lead]
        traj[f"T-{lead}"] = {
            "n": len(xs),
            "dist_ticks_p10": _pct(xs, 0.10),
            "dist_ticks_p50": _pct(xs, 0.50),
            "dist_ticks_p90": _pct(xs, 0.90),
            "share_fill_price_at_or_below_bid": (
                round(sum(at_or_better[lead]) / len(at_or_better[lead]), 4)
                if at_or_better[lead] else None),
            "spread_ticks_p50": _pct(spread[lead], 0.50),
        }
    rep: Dict[str, Any] = {
        "maker_fills_measured": n,
        "trajectory": traj,
        "post_fill_mid_move": {
            f"T+{k}": {"n": len(after[k]), "p10": _pct(after[k], 0.10),
                       "p50": _pct(after[k], 0.50),
                       "p90": _pct(after[k], 0.90),
                       "mean": (round(sum(after[k]) / len(after[k]), 5)
                                if after[k] else None)}
            for k in AFTER},
        "timestamp_uncertainty": {
            "basis": "real block timestamps, never interpolated",
            "n_block_gaps": len(block_gaps),
            "block_gap_s_p50": _pct(block_gaps, 0.50),
            "block_gap_s_p90": _pct(block_gaps, 0.90),
            "note": ("the fill's true moment lies inside its block's "
                     "interval, so each lead carries about this much slack"),
        },
        "LABEL": "OBSERVED_BOOK_AND_FILLS_NOT_L5_ORDERS",
        "labels": ("dist_ticks is (own best bid - fill price); it is a "
                   "distance between two public numbers and is not asserted "
                   "to be where any order rested. A positive post-fill mid "
                   "move is in the buyer's favour."),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({"maker_fills_measured": n,
                      "post_fill_mid_move": rep["post_fill_mid_move"],
                      "timestamp_uncertainty": rep["timestamp_uncertainty"]},
                     indent=1, default=str))
    for lead in LEADS:
        r = traj[f"T-{lead}"]
        print(f"T-{lead:<2} n={r['n']:<5} dist p10/p50/p90 = "
              f"{r['dist_ticks_p10']}/{r['dist_ticks_p50']}/"
              f"{r['dist_ticks_p90']}  resting-consistent="
              f"{r['share_fill_price_at_or_below_bid']}  "
              f"spread={r['spread_ticks_p50']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

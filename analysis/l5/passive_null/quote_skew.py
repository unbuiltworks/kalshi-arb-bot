"""D: does L5's eventual fill offset from the prior BBO move with his inventory?

H2 (SKEW) predicts it does: a maker who is long Up should quote Up worse and
Down better, so his eventual Up fills should sit FURTHER below the touch as his
Up inventory grows. H1 and H3 predict no such relationship.

THE CLAIM IS DELIBERATELY WEAK, because the data cannot support a strong one.
We cannot identify L5's resting orders in an anonymous book, so nothing here
says "his order was at X". The measured quantity is exactly:

    eventual L5 fill price, relative to the BBO observed BEFORE that fill

and no anonymous size is attributed to him anywhere.

MINT FILLS ARE EXCLUDED. 26% of his maker buys price at or above the prevailing
ask because they are complete-set mints against a complement buyer, not resting
bids. Those have no measurable depth, and including them would put a constant
in the middle of a regression about depth.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

LEAD_S = 5


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times",
                    default="analysis/l5/historical_l2/block_times.json")
    ap.add_argument("--out", default="analysis/l5/passive_null/quote_skew.json")
    a = ap.parse_args(argv)

    import pyarrow.parquet as pq
    from analysis.l5.historical_l2.bbo_offset import (Book, offset_ticks,
                                                      placement_consistency)
    d = pq.read_table(a.parquet).to_pydict()
    books: Dict[str, Book] = defaultdict(Book)
    outcome: Dict[str, str] = {}
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        tid = str(d["token_id"][i])
        outcome[tid] = str(d["outcome"][i])
        books[tid].add(d["captured_at"][i].timestamp(),
                       {"best_bid": d["best_bid"][i],
                        "best_ask": d["best_ask"][i],
                        "bid_prices": d["bid_prices"][i],
                        "bid_sizes": d["bid_sizes"][i]})
    for b in books.values():
        b.sort()
    bt = json.load(open(a.block_times))["block_times"]
    bf = json.load(open(a.backfill))

    rows: List[Dict[str, Any]] = []
    for w in bf["rows"]:
        fills = sorted(w["fills"], key=lambda f: (f.get("block") or 0,
                                                  f.get("log_index") or 0))
        if not fills:
            continue
        qty: Dict[str, float] = defaultdict(float)
        for f in fills:
            tid = str(f["token_id"])
            out = outcome.get(tid)
            t = bt.get(str(f.get("block")))
            if out is None or t is None or f.get("l5_role") != "maker":
                qty[out or "?"] += float(f["size"])
                continue
            bk = books.get(tid)
            snap = bk.at(float(t) - LEAD_S) if bk else None
            cls = placement_consistency(snap, f["price"])
            up, dn = qty.get("Up", 0.0), qty.get("Down", 0.0)
            tot = up + dn
            signed = ((up - dn) / tot) if tot > 0 else 0.0
            # inventory on THIS fill's own side, signed so positive = already
            # long the side he is now buying more of
            own = signed if out == "Up" else -signed
            if cls == "RESTING_BID_CONSISTENT" and snap:
                rows.append({
                    "slug": w["market_slug"], "outcome": out,
                    "offset_ticks": offset_ticks(snap["best_bid"], f["price"]),
                    "signed_imbalance": round(signed, 4),
                    "own_side_imbalance": round(own, 4),
                    "tot_qty": round(tot, 2),
                })
            qty[out] += float(f["size"])

    def _corr(xs, ys):
        n = len(xs)
        if n < 3:
            return None
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        sx = statistics.pstdev(xs) or 1e-12
        sy = statistics.pstdev(ys) or 1e-12
        return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                     / (n * sx * sy), 4)

    good = [r for r in rows if r["offset_ticks"] is not None and r["tot_qty"] > 0]
    xs = [r["own_side_imbalance"] for r in good]
    ys = [float(r["offset_ticks"]) for r in good]
    buckets: Dict[str, List[float]] = defaultdict(list)
    for r in good:
        o = r["own_side_imbalance"]
        k = ("<=-0.20" if o <= -0.20 else "-0.20..-0.05" if o <= -0.05
             else "-0.05..0.05" if o < 0.05 else "0.05..0.20" if o < 0.20
             else ">=0.20")
        buckets[k].append(float(r["offset_ticks"]))
    order = ["<=-0.20", "-0.20..-0.05", "-0.05..0.05", "0.05..0.20", ">=0.20"]
    rep = {
        "n_measurable_resting_bid_fills": len(good),
        "lead_s": LEAD_S,
        "correlation_own_side_imbalance_vs_offset": _corr(xs, ys),
        "by_own_side_imbalance": {
            k: {"n": len(buckets[k]),
                "median_offset_ticks": (statistics.median(buckets[k])
                                        if buckets[k] else None),
                "mean_offset_ticks": (round(statistics.fmean(buckets[k]), 3)
                                      if buckets[k] else None)}
            for k in order if buckets.get(k)},
        "reads": ("H2 predicts a POSITIVE correlation: the longer he already is "
                  "of a side, the deeper below the touch his further fills on "
                  "that side should sit. A flat profile is evidence against "
                  "inventory-skewed quoting."),
        "never": ("eventual FILL PRICE relative to a PRIOR BBO. No anonymous "
                  "book size is attributed to L5 or anyone else; mint fills "
                  "are excluded because they have no measurable depth."),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump({**rep, "rows": good[:300]}, fh, indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

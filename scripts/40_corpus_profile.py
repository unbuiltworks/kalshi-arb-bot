"""Stage D -- a purely DESCRIPTIVE profile of the corpus.

This exists so an analyst who has never seen the data can orient quickly
without us steering them. It reports distributions and cross-tabs only.

It deliberately contains NO hypothesis, NO causal claim and NO conclusion
of ours. Where a number could be read as an argument, it is reported as a
bare distribution instead. The reader is told what is in the files, not
what we think it means.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import gzip
import json
import math
import os
import statistics
import sys


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def hist(vals, edges):
    c = collections.Counter()
    for v in vals:
        for lo, hi in zip(edges, edges[1:]):
            if lo <= v < hi:
                c[f"[{lo},{hi})"] += 1
                break
        else:
            c[f">={edges[-1]}"] += 1
    return dict(c)


def q(vals, ps=(0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)):
    if not vals:
        return {}
    s = sorted(vals)
    return {f"p{int(p*100)}": round(s[min(len(s) - 1, int(len(s) * p))], 6)
            for p in ps}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    prices, sizes, gaps, legs = [], [], [], collections.Counter()
    pair_costs = []
    by_month = collections.Counter()
    by_hour = collections.Counter()
    by_sec_bucket = collections.Counter()
    size_values = collections.Counter()
    price_values = collections.Counter()
    switch_by_gap = collections.Counter()
    n = 0
    for p in sorted(glob.glob(os.path.join(a.dataset, "fills",
                                           "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                n += 1
                px, sz = f(r["price"]), f(r["size"])
                if px is not None:
                    prices.append(px)
                    price_values[round(px, 3)] += 1
                if sz is not None:
                    sizes.append(sz)
                    size_values[round(sz, 4)] += 1
                legs[r["leg"]] += 1
                by_month[r["datetime_utc"][:7]] += 1
                by_hour[r["datetime_utc"][11:13]] += 1
                g = r["seconds_since_prev_fill"]
                if g not in ("", None):
                    gi = int(g)
                    gaps.append(gi)
                    sw = int(r["leg_switched_from_prev"] or 0)
                    switch_by_gap[(min(gi, 60), sw)] += 1
                pc = f(r["effective_pair_cost"])
                if pc is not None:
                    pair_costs.append(pc)
                s = r["seconds_from_window_start"]
                d = f(r["duration_minutes"])
                if s not in ("", None) and d:
                    frac = int(f(s) / (d * 60) * 10)
                    by_sec_bucket[f"decile_{min(max(frac,0),10)}"] += 1

    wins = list(csv.DictReader(
        gzip.open(os.path.join(a.dataset, "l5_window_summary.csv.gz"), "rt")))
    wpnl = [f(w["window_pnl"]) for w in wins if w["window_pnl"]]
    wpair = [f(w["final_pair_cost"]) for w in wins if w["final_pair_cost"]]
    wnf = [int(w["n_fills"]) for w in wins]
    wres = [f(w["final_residual_qty"], 0.0) for w in wins]
    wspend = [f(w["total_spend"], 0.0) for w in wins]

    cash = collections.Counter()
    cash_usd = collections.Counter()
    with open(os.path.join(a.work, "cashflow.csv"), newline="",
              encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            cash[r["type"]] += 1
            cash_usd[r["type"]] += f(r["usdc_size"], 0.0)

    prof = {
        "_note": "Descriptive only. No hypothesis, no causal claim, no "
                 "conclusion of the package authors appears in this file.",
        "fills": {
            "count": n,
            "legs": dict(legs),
            "by_month": dict(sorted(by_month.items())),
            "by_utc_hour": dict(sorted(by_hour.items())),
            "position_in_window_deciles": dict(sorted(by_sec_bucket.items())),
            "price": {"quantiles": q(prices),
                      "mean": round(statistics.fmean(prices), 6),
                      "histogram_0_to_1_by_0.05":
                          hist(prices, [i / 20 for i in range(21)]),
                      "20_most_common_values":
                          dict(price_values.most_common(20))},
            "size": {"quantiles": q(sizes),
                     "mean": round(statistics.fmean(sizes), 6),
                     "20_most_common_values":
                         dict(size_values.most_common(20))},
            "seconds_since_prev_fill_same_window": {
                "quantiles": q([float(g) for g in gaps]),
                "histogram": hist([float(g) for g in gaps],
                                  [0, 1, 2, 3, 5, 8, 10, 15, 20, 30, 60,
                                   120, 300])},
            "effective_pair_cost_after_fill": {
                "quantiles": q(pair_costs),
                "histogram": hist(pair_costs,
                                  [0.5, 0.8, 0.9, 0.95, 0.98, 1.0, 1.02,
                                   1.05, 1.1, 1.2, 1.5])},
        },
        "windows": {
            "count": len(wins),
            "by_asset": dict(collections.Counter(w["asset"] for w in wins)),
            "by_duration_minutes":
                dict(collections.Counter(w["duration_minutes"]
                                         for w in wins)),
            "by_month":
                dict(sorted(collections.Counter(
                    w["window_start_utc"][:7] for w in wins).items())),
            "n_fills": {"quantiles": q([float(x) for x in wnf]),
                        "max": max(wnf)},
            "total_spend_usdc": {"quantiles": q(wspend),
                                 "sum": round(sum(wspend), 2)},
            "final_pair_cost": {"quantiles": q(wpair)},
            "final_residual_qty": {"quantiles": q(wres)},
            "window_pnl_usdc": {
                "quantiles": q(wpnl), "sum": round(sum(wpnl), 2),
                "n_positive": sum(1 for x in wpnl if x > 0),
                "n_negative": sum(1 for x in wpnl if x < 0),
                "n_zero": sum(1 for x in wpnl if x == 0),
                "histogram": hist(wpnl, [-1000, -300, -100, -30, -10, -1, 0,
                                         1, 10, 30, 100, 300, 1000])},
            "ended_with_zero_residual":
                sum(1 for w in wins if f(w["final_residual_qty"], 0.0) <= 1e-9),
            "with_at_least_one_leg_switch":
                sum(1 for w in wins if int(w["n_leg_switches"] or 0) > 0),
            "settlement_evidence":
                dict(collections.Counter(w["settlement_evidence"]
                                         for w in wins)),
        },
        "account_level_cashflow_records": {
            "counts": dict(cash),
            "usdc_totals": {k: round(v, 4) for k, v in cash_usd.items()},
            "_caveat": "MAKER_REBATE / TAKER_REBATE / REWARD records carry "
                       "an EMPTY slug and conditionId, so they cannot be "
                       "attributed to any window or market. REDEEM records "
                       "do carry a slug.",
        },
    }

    with open(os.path.join(a.out, "CORPUS_PROFILE.json"), "w",
              encoding="utf-8") as fh:
        json.dump(prof, fh, indent=1)
    print("wrote CORPUS_PROFILE.json",
          os.path.getsize(os.path.join(a.out, "CORPUS_PROFILE.json")), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

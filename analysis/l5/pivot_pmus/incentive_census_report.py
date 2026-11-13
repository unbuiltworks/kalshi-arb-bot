#!/usr/bin/env python3
"""PUBLISHED census tables from raw/incentives_census.json.gz. Offline.

Pools are as published per time period. "daily" and "daily_event" periods
pay their pool per ET calendar day; "early", "day_of", "live" pay their pool
once per game period (period lengths: early = listing..start-6h,
day_of = start-6h..start, live = start..settlement, per the venue doc copy
data/pmus/sports_incentives_2026-09-10/doc_incentives_liquidity.md).

    python3 analysis/l5/pivot_pmus/incentive_census_report.py
"""
import gzip
import json
import os
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY = ("daily", "daily_event")


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def dist(xs):
    return {"min": q(xs, 0), "p25": q(xs, .25), "median": q(xs, .5), "p75": q(xs, .75), "max": q(xs, 1.0)} if xs else None


def main():
    d = json.load(gzip.open(os.path.join(HERE, "raw", "incentives_census.json.gz")))
    ev = json.load(gzip.open(os.path.join(HERE, "raw", "all_events_slim.json.gz")))
    open_by_cat = Counter(e.get("category") for e in ev["events"] for m in e["markets"] if not m.get("closed"))
    per_mkt = defaultdict(lambda: {"daily": 0.0, "game": defaultdict(float), "tps": []})
    for r in d["rows"]:
        tp = r["tp"]
        m = per_mkt[(r["event_category"], r["marketSlug"])]
        if tp.get("period") in DAILY:
            m["daily"] += float(tp.get("rewardPool") or 0)
        else:
            m["game"][tp.get("period")] += float(tp.get("rewardPool") or 0)
        m["tps"].append(tp)
    cats = defaultdict(list)
    for (c, s), m in per_mkt.items():
        cats[c].append((s, m))
    out = {"label": d["label"], "fetched": [d["started"], d["ended"]], "open_markets_queried": d["open_markets_queried"],
           "by_category": {}}
    for c, lst in sorted(cats.items(), key=lambda x: -len(x[1])):
        tps = [tp for _, m in lst for tp in m["tps"]]
        game_tot = defaultdict(float)
        for _, m in lst:
            for k, v in m["game"].items():
                game_tot[k] += v
        sub = Counter()
        for r in d["rows"]:
            if r["event_category"] == c:
                sub[r.get("subcategory") or "-"] += 1
        out["by_category"][c] = {
            "open_markets": open_by_cat.get(c, 0), "markets_with_program": len(lst),
            "program_period_rows": len(tps),
            "sum_daily_pool_usd_per_day": round(sum(m["daily"] for _, m in lst), 2),
            "sum_game_period_pools_usd": {k: round(v, 2) for k, v in game_tot.items()},
            "per_market_daily_pool": dist([m["daily"] for _, m in lst if m["daily"] > 0]),
            "per_market_game_pool_by_period": {p: dist([m["game"][p] for _, m in lst if m["game"].get(p)]) for p in game_tot},
            "targetSize": dist([float(tp["targetSize"]) for tp in tps if tp.get("targetSize") is not None]),
            "discountFactor": dist([float(tp["discountFactor"]) for tp in tps if tp.get("discountFactor") is not None]),
            "maxSpread": dist([float(tp["maxSpread"]) for tp in tps if tp.get("maxSpread") is not None]),
            "share_rows_with_maxSpread": round(sum("maxSpread" in tp for tp in tps) / len(tps), 3),
            "top_subcategories": sub.most_common(8),
            "top_programs": Counter(tp.get("programId") for tp in tps).most_common(6)}
    json.dump(out, open(os.path.join(HERE, "incentive_census_summary.json"), "w"), indent=1)
    print("%-12s %7s %7s %10s %s" % ("category", "open", "w/prog", "daily$/day", "game-period pools $"))
    for c, s in out["by_category"].items():
        print("%-12s %7d %7d %10.0f %s" % (c, s["open_markets"], s["markets_with_program"],
              s["sum_daily_pool_usd_per_day"], s["sum_game_period_pools_usd"]))
    print(json.dumps(out, indent=1)[:9000])


if __name__ == "__main__":
    main()

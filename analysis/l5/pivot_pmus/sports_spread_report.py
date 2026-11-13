#!/usr/bin/env python3
"""SNAPSHOT report: spread distribution from raw/sports_book_snapshots.json.
Offline. A book with a side missing or a spread >= 50c is counted as
UNQUOTED (a stub, not a tradeable spread) and kept out of the distribution.

    python3 analysis/l5/pivot_pmus/sports_spread_report.py
"""
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def stats(xs, unq):
    s = {"quoted_obs": len(xs), "unquoted_obs": unq}
    if xs:
        s.update(median_c=round(100 * q(xs, .5), 2), p75_c=round(100 * q(xs, .75), 2),
                 p90_c=round(100 * q(xs, .9), 2),
                 ge_3c=round(sum(x >= 0.0299 for x in xs) / len(xs), 3),
                 ge_5c=round(sum(x >= 0.0499 for x in xs) / len(xs), 3))
    return s


def main():
    d = json.load(open(os.path.join(HERE, "raw", "sports_book_snapshots.json")))
    meta = {r["slug"]: r for r in d["sample"]}
    groups, unq = defaultdict(list), defaultdict(int)
    per_mkt = defaultdict(list)
    touch = defaultdict(list)
    for s in d["snapshots"]:
        m = meta[s["slug"]]
        keys = ["ALL", "sport:" + m["sport"], "phase:" + m["phase"], "league:" + m["league"]]
        sp = s.get("spread")
        if sp is None or sp >= 0.5:
            for k in keys:
                unq[k] += 1
            per_mkt[s["slug"]].append(None)
            continue
        per_mkt[s["slug"]].append(sp)
        for k in keys:
            groups[k].append(sp)
        touch[m["phase"]].append(min(s.get("bid_qty", 0), s.get("ask_qty", 0)))
    out = {"label": d["label"], "times": [d["snapshots"][0]["t"], d["snapshots"][-1]["t"]],
           "groups": {k: stats(groups.get(k, []), unq.get(k, 0))
                      for k in sorted(set(groups) | set(unq), key=lambda k: (k != "ALL", k.split(":")[0], -len(groups.get(k, []))))}}
    persist = [sl for sl, v in per_mkt.items() if all(x is not None and x >= 0.0299 for x in v)]
    out["markets_ge_3c_all_rounds"] = [{"slug": sl, "phase": meta[sl]["phase"], "spreads": per_mkt[sl]} for sl in persist]
    out["median_min_touch_qty"] = {k: sorted(v)[len(v) // 2] for k, v in touch.items() if v}
    out["per_market"] = {sl: {"phase": meta[sl]["phase"], "sport": meta[sl]["sport"], "spreads": v}
                         for sl, v in per_mkt.items()}
    json.dump(out, open(os.path.join(HERE, "sports_spread_summary.json"), "w"), indent=1)
    print("%-28s %6s %5s %7s %6s %6s %6s %6s" % ("group", "quoted", "unq", "med_c", "p75_c", "p90_c", ">=3c", ">=5c"))
    for k, s in out["groups"].items():
        print("%-28s %6d %5d %7s %6s %6s %6s %6s" % (k, s["quoted_obs"], s["unquoted_obs"], s.get("median_c"),
              s.get("p75_c"), s.get("p90_c"), s.get("ge_3c"), s.get("ge_5c")))
    print("markets >= 3c in every round:", len(persist), [ (x["slug"], x["spreads"]) for x in out["markets_ge_3c_all_rounds"]])
    print("median min(touch qty):", out["median_min_touch_qty"])


if __name__ == "__main__":
    main()

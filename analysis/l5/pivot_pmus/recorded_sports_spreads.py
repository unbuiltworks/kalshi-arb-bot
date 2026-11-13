#!/usr/bin/env python3
"""RECORDED (offline): spread distribution in the PMUS sports book captures
already in the repo. No network.

  data/pmus/sports_opportunity_2026-09-10/snapshot_{A,B}.json.gz
      full books for ~340 game markets, 2026-09-10 18:15Z and 18:25Z
  data/transfer_backtest/pmus_live_sample.jsonl
      2 MLB markets, 2026-09-02 18:23-18:35Z, ~6 s top-of-book rows

Neither carries a settled outcome or covers a market from first row to close,
so the pair-gate replay cannot be run on them.

    python3 analysis/l5/pivot_pmus/recorded_sports_spreads.py
"""
import gzip
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def stats(xs):
    if not xs:
        return None
    return {"n": len(xs), "median_c": round(100 * q(xs, .5), 2), "p75_c": round(100 * q(xs, .75), 2),
            "p90_c": round(100 * q(xs, .9), 2),
            "share_ge_3c": round(sum(s >= 0.0299 for s in xs) / len(xs), 3),
            "share_ge_5c": round(sum(s >= 0.0499 for s in xs) / len(xs), 3)}


def main():
    out = {"label": "RECORDED snapshots (offline), not a tape with outcomes"}
    by = defaultdict(list)
    meta = {}
    for tag in ("A", "B"):
        d = json.load(gzip.open(os.path.join(ROOT, "data/pmus/sports_opportunity_2026-09-10/snapshot_%s.json.gz" % tag)))
        meta[tag] = {k: d.get(k) for k in ("started", "ended")}
        book = d[[k for k in d if isinstance(d[k], dict) and len(d[k]) > 50][0]]
        for slug, m in book.items():
            if not isinstance(m, dict) or not m.get("bids") or not m.get("offers"):
                continue
            b = max(float(l["px"]) for l in m["bids"]); a = min(float(l["px"]) for l in m["offers"])
            lg = slug.split("-")[1]
            by[lg].append(a - b); by["ALL"].append(a - b)
    out["snapshot_2026_09_10"] = {"times": meta, "by_league": {k: stats(v) for k, v in sorted(by.items(), key=lambda x: -len(x[1]))}}
    rows = [json.loads(l) for l in open(os.path.join(ROOT, "data/transfer_backtest/pmus_live_sample.jsonl"))]
    mlb = defaultdict(list)
    for r in rows:
        if r.get("bid") and r.get("ask"):
            mlb[r["slug"]].append(float(r["ask"]) - float(r["bid"]))
    out["mlb_sample_2026_09_02"] = {s: stats(v) for s, v in mlb.items()}
    print(json.dumps(out, indent=1))
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "recorded_sports_spreads.json"), "w"), indent=1)


if __name__ == "__main__":
    main()

"""Rest at SEVERAL prices at once (8/16/20) instead of one. READ-ONLY.

Owner asked 2026-11-14: if 0.08 and 0.16 are both positive, why not rest
both? Settlement P&L is linear in legs, so a ladder's expectancy IS the
sum of its rungs' -- the question is never whether the sum is positive,
it is what the sum earns PER DOLLAR AT RISK, because exposure is what the
caps actually ration (SERIES_MAX_TOTAL_EXPOSURE_USD $5, approval $1 a
window).

Same tape, same adverse fill rule. A rung fills when that side's bid
falls through it, so a falling side fills the HIGH rungs first and the
cheap one last -- a ladder averages INTO the collapsing side.
"""
from __future__ import annotations

import gzip
import json
import math
import os
from collections import defaultdict

TAPE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "btc5m_aligned_20261112.jsonl.gz")
CANCEL_LEAD_S = 30.0


def load():
    w = defaultdict(lambda: {"outcome": None, "min_up": None, "min_down": None})
    with gzip.open(TAPE, "rt") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            mkt, rem = d.get("market"), d.get("seconds_remaining")
            if not mkt or rem is None or float(rem) < CANCEL_LEAD_S:
                continue
            rec = w[mkt]
            if d.get("settled_outcome"):
                rec["outcome"] = str(d["settled_outcome"])
            for side, key in (("up", "min_up"), ("down", "min_down")):
                b = (d.get(side) or {}).get("bid")
                if b is None:
                    continue
                b = float(b)
                if rec[key] is None or b < rec[key]:
                    rec[key] = b
    return {m: r for m, r in w.items()
            if r["outcome"] in ("UP", "DOWN")
            and r["min_up"] is not None and r["min_down"] is not None}


def run(windows, rungs):
    """One share at EACH rung on EACH side. Returns (net c/w, t, at-risk)."""
    series = []
    for rec in windows.values():
        cost = 0.0
        payout = 0.0
        for side, key in (("UP", "min_up"), ("DOWN", "min_down")):
            for p in rungs:
                if rec[key] <= p + 1e-12:
                    cost += p
                    if rec["outcome"] == side:
                        payout += 1.0
        series.append(payout - cost)
    n = len(series)
    mean = sum(series) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in series) / (n - 1))
    t = mean / (sd / math.sqrt(n))
    at_risk = sum(rungs) * 2 * 100.0   # CENTS at risk, both sides
    return 100.0 * mean, t, at_risk


def main() -> int:
    w = load()
    print("windows: %d\n" % len(w))
    BUDGET = 100.0   # cents of approved exposure per window ($1.00)
    print("%-26s %8s %7s %9s %10s %14s" %
          ("rungs", "net c/w", "t", "at risk c", "net per c", "c/w at $1 cap"))
    for label, rungs in (("0.08 only", (0.08,)),
                         ("0.16 only", (0.16,)),
                         ("0.20 only", (0.20,)),
                         ("0.08 + 0.16", (0.08, 0.16)),
                         ("0.08 + 0.16 + 0.20", (0.08, 0.16, 0.20)),
                         ("0.08 + 0.12 + 0.16 + 0.20", (0.08, 0.12, 0.16, 0.20))):
        m, t, ar = run(w, rungs)
        sets = BUDGET / ar            # how many share-sets fit the cap
        print("%-26s %+8.2f %+7.2f %9.1f %10.3f %+14.1f"
              % (label, m, t, ar, m / ar, m * sets))
    print()
    print("'net per c' is net c/w per cent at risk -- what the exposure cap")
    print("actually rations. 'c/w at $1 cap' scales each to the SAME $1.00 of")
    print("approved per-window exposure, which is the real comparison.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

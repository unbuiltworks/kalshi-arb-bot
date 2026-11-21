"""Rest the two sides at DIFFERENT prices. READ-ONLY.

THE QUESTION, owner 2026-11-14: if we rest 0.20 on one side and 0.08 on
the other and both fill, is that a pair? YES -- any two opposite legs
redeem exactly 1.00, so the pair costs 0.28 and locks +0.72. And the 0.20
leg fills far more often than an 0.08 leg, because its side only has to
dip to 0.20. Smaller round trip, more pairs, smaller prize.

WHY IT IS NOT ALREADY TESTED. The full sweep rests BOTH sides at the same
price. `side_asymmetry.py` measured each side separately but still at a
common price. Neither can see an asymmetric COMBINATION.

THE CONFOUND THIS CONTROLS FOR. Measuring UP@p1/DOWN@p2 on one tape reads
the tape's drift: DOWN settled 56.1% of these 187 windows, which is what
made the per-side "edge" look real at t +2.10 before the base rate
explained it. So every pairing here is evaluated BOTH WAYS -- UP@p1 with
DOWN@p2 and UP@p2 with DOWN@p1 -- and averaged. A direction-neutral
reading cannot be a direction artifact.
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


def one_assignment(windows, p_up, p_down):
    """UP rests at p_up, DOWN at p_down."""
    series = []
    pairs = 0
    for rec in windows.values():
        cost = 0.0
        held = {"UP": 0, "DOWN": 0}
        if rec["min_up"] <= p_up + 1e-12:
            held["UP"] = 1
            cost += p_up
        if rec["min_down"] <= p_down + 1e-12:
            held["DOWN"] = 1
            cost += p_down
        if held["UP"] and held["DOWN"]:
            pairs += 1
        series.append(float(held[rec["outcome"]]) - cost)
    return series, pairs


def both_ways(windows, pa, pb):
    """Average the two assignments, so tape drift cannot favour either."""
    s1, k1 = one_assignment(windows, pa, pb)
    s2, k2 = one_assignment(windows, pb, pa)
    series = [(x + y) / 2.0 for x, y in zip(s1, s2)]
    n = len(series)
    m = sum(series) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in series) / (n - 1))
    t = m / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    return 100.0 * m, t, (k1 + k2) / 2.0, n


def main() -> int:
    w = load()
    print("windows: %d   (direction-neutral: every pairing averaged both ways)\n"
          % len(w))
    combos = [(0.08, 0.08), (0.08, 0.12), (0.08, 0.16), (0.08, 0.20),
              (0.08, 0.25), (0.08, 0.30), (0.12, 0.16), (0.12, 0.20),
              (0.16, 0.20), (0.05, 0.20), (0.05, 0.30), (0.03, 0.30)]
    print("%-14s %9s %8s %9s %8s %9s"
          % ("rest at", "pair cost", "locks", "pair rate", "net c/w", "t"))
    for pa, pb in combos:
        m, t, k, n = both_ways(w, pa, pb)
        tag = "%.2f / %.2f" % (pa, pb)
        mark = "  <-- current" if (pa, pb) == (0.08, 0.08) else ""
        print("%-14s %9.2f %+8.2f %8.1f%% %+9.2f %+8.2f%s"
              % (tag, pa + pb, 1 - (pa + pb), 100.0 * k / n, m, t, mark))
    print()
    print("%d combinations tested -> bar is sqrt(2 ln %d) = %.2f"
          % (len(combos), len(combos), math.sqrt(2 * math.log(len(combos)))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

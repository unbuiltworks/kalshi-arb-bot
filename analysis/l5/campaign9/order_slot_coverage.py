#!/usr/bin/env python3
"""How much of the clock did we actually have an order in the market?

Read-only, from `campaign8/c8_joined.json` -- transmitted orders with their
venue-confirmed terminal state and measured dwell. Nothing modelled.

WHY THIS AND NOT DWELL. Longer completion dwell was tested and came back
NOT SUPPORTED: the settled break-even completion pair cost is 0.9532 and the
achieved quantity-weighted cost was 0.9530, so completing and stranding are
a dead heat at the prices we quoted. Pricing is not the lever. This measures
the other axis -- not how long each order rested, but what fraction of the
window had any order resting at all.

COVERAGE OF THE CLOCK is the honest denominator: 900 s per window, whether
or not the engine had decided to be active. Coverage of the SPAN (first
submission to last terminal) is reported beside it, because the two differ
by the time the engine spent not starting and finishing early, which is a
separate loss with a separate cause.

This counts order-slot occupancy, NOT fills and NOT profit. A resting order
is an opportunity to be filled, never a fill: the conversion from coverage
to pairs is an assumption, and it is labelled as one wherever it is used.
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[3]
WINDOW_S = 900.0


def main() -> int:
    rows = json.loads(
        (ROOT / "analysis/l5/campaign8/c8_joined.json").read_text())
    by = defaultdict(list)
    for r in rows:
        by[r["win"]].append(r)

    out = {}
    tot_cov = tot_span = 0.0
    all_gaps = []
    for win in sorted(by):
        rs = sorted(by[win], key=lambda r: r["tsub"])
        # merged, because one-order concurrency SHOULD make these disjoint;
        # merging rather than summing means an overlap cannot inflate it.
        merged = []
        for r in rs:
            a, b = r["tsub"], r["tsub"] + r["dwell"]
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        span = merged[-1][1] - merged[0][0]
        cov = sum(b - a for a, b in merged)
        gaps = [merged[i + 1][0] - merged[i][1]
                for i in range(len(merged) - 1)]
        # the submission CADENCE, which is the thing that turns out to be
        # constant: submit-to-submit, not terminal-to-submit.
        cadence = [rs[i + 1]["tsub"] - rs[i]["tsub"]
                   for i in range(len(rs) - 1)]
        all_gaps.extend(gaps)
        out[win] = {
            "orders": len(rs),
            "span_s": round(span, 1),
            "covered_s": round(cov, 1),
            "coverage_of_span_pct": round(100 * cov / span, 1) if span else None,
            "coverage_of_clock_pct": round(100 * cov / WINDOW_S, 1),
            "gaps_n": len(gaps),
            "gap_median_s": round(sorted(gaps)[len(gaps) // 2], 1) if gaps else None,
            "gap_max_s": round(max(gaps), 1) if gaps else None,
            "cadence_median_s": (round(sorted(cadence)[len(cadence) // 2], 1)
                                 if cadence else None),
        }
        tot_cov += cov
        tot_span += span

    n = len(by)
    g = sorted(all_gaps)
    print(json.dumps({
        "windows": out,
        "totals": {
            "windows": n,
            "clock_s": n * WINDOW_S,
            "covered_s": round(tot_cov, 1),
            "coverage_of_clock_pct": round(100 * tot_cov / (n * WINDOW_S), 1),
            "coverage_of_span_pct": round(100 * tot_cov / tot_span, 1),
            "gap_median_s": round(g[len(g) // 2], 1) if g else None,
            "note": ("coverage is order-slot occupancy, not fills and not "
                     "profit; a resting order is an opportunity to be "
                     "filled, never a fill"),
        },
    }, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

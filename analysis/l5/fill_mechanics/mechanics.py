"""WHY DOES L5 GET FILLS AND WE DO NOT? Same venue, same series, same books.

The owner's correction is the premise: PMUS execution is PROVEN, by L5's own
transactions. So the question is mechanical, and it is answerable from the
corpus without any new collection.

WHAT THIS CAN AND CANNOT SEE. Every row is an EXECUTION. An execution tells us
a resting order existed AT OR BEFORE that moment -- a fill time is an UPPER
BOUND on submit time, never the submit time itself. Queue position is never
published by the venue and is never claimed here. So:

  OBSERVED   fill timestamp, outcome, price, size, usdcSize, window
  INFERRED   that an order rested at that price at or before the fill
  UNKNOWABLE when it was submitted, where it sat in the queue, what was
             cancelled or replaced unfilled, and every order that never filled

Two signatures survive that limit and are the whole point of this file:

  (1) SAME-SECOND MULTI-LEVEL FILLS. Two fills on one side, same second,
      different prices, cannot come from one order. They are a RESTING LADDER
      being swept. Depth of the ladder is directly observable.
  (2) FIRST-FILL TIME. A fill n seconds into a 900s window proves an order was
      resting by second n. The distribution of that bounds how early he rests.
"""
from __future__ import annotations

import collections
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "inventory_trajectory"))
from trajectory import load, WINDOW_S                        # noqa: E402

CAPTURES = ["data/l5/raw/activity.jsonl", "data/l5/raw/activity_gap.jsonl.gz"]


def start_ts(slug):
    try:
        return int(slug.rsplit("-", 1)[1])
    except Exception:                                         # noqa: BLE001
        return None


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def dist(name, v, fmt="%.2f"):
    if not v:
        print("  %-34s (none)" % name)
        return
    print(("  %-34s n=%-7d p10=" + fmt + "  median=" + fmt + "  p90=" + fmt
           + "  max=" + fmt)
          % (name, len(v), pct(v, .10), st.median(v), pct(v, .90), max(v)))


def main():
    wins = load(CAPTURES)
    print("windows %d   fills %d" % (len(wins), sum(len(v) for v in wins.values())))

    first_fill_s, all_fill_s, sizes, prices, gaps = [], [], [], [], []
    levels_per_side, ladder_depths, both_sides_s = [], [], []
    swept_fills = swept_windows = 0
    peak_resid_contracts, peak_capital = [], []

    for slug, fills in wins.items():
        t0 = start_ts(slug)
        if t0 is None:
            continue
        ts = [f.get("timestamp") for f in fills]
        if not ts or any(t is None for t in ts):
            continue
        offs = [t - t0 for t in ts]
        if min(offs) < 0 or max(offs) > WINDOW_S + 120:
            continue                      # slug/time mismatch: drop, never guess
        first_fill_s.append(min(offs))
        all_fill_s.extend(offs)
        gaps.extend(b - a for a, b in zip(sorted(offs), sorted(offs)[1:]))

        # a ladder is only visible when one side fills at two prices in one second
        by_sec = collections.defaultdict(lambda: collections.defaultdict(set))
        lv = collections.defaultdict(set)
        up_q = dn_q = 0.0
        up_c = dn_c = 0.0
        peak_r = peak_k = 0.0
        seen_up = seen_dn = None
        for f, off in zip(fills, offs):
            side = "Up" if str(f.get("outcome")) == "Up" else "Down"
            px = float(f.get("price") or 0)
            sz = float(f.get("size") or 0)
            usd = float(f.get("usdcSize") or 0)
            sizes.append(sz)
            prices.append(px)
            lv[side].add(round(px, 4))
            by_sec[f.get("timestamp")][side].add(round(px, 4))
            if side == "Up":
                up_q += sz; up_c += usd
                seen_up = off if seen_up is None else seen_up
            else:
                dn_q += sz; dn_c += usd
                seen_dn = off if seen_dn is None else seen_dn
            peak_r = max(peak_r, abs(up_q - dn_q))
            peak_k = max(peak_k, up_c + dn_c - min(up_q, dn_q))  # cash at risk
        if seen_up is not None and seen_dn is not None:
            both_sides_s.append(max(seen_up, seen_dn))
        for side, s in lv.items():
            levels_per_side.append(len(s))
        hit = False
        for _, sides in by_sec.items():
            for side, pxs in sides.items():
                if len(pxs) >= 2:
                    ladder_depths.append(len(pxs))
                    swept_fills += 1
                    hit = True
        swept_windows += 1 if hit else 0
        peak_resid_contracts.append(peak_r)
        peak_capital.append(peak_k)

    n = len(first_fill_s)
    print("usable windows %d" % n)
    print()
    print("=== 1. HOW EARLY IS HE RESTING?  (fill time bounds submit time) ===")
    dist("FIRST fill, seconds into window", first_fill_s, "%.0f")
    dist("BOTH sides live by, seconds", both_sides_s, "%.0f")
    for thr in (5, 15, 30, 60, 120):
        k = sum(1 for v in first_fill_s if v <= thr)
        print("  first fill within %4ds of open: %d / %d = %.1f%%"
              % (thr, k, n, 100 * k / n))
    print()
    print("=== 2. IS IT A LADDER?  (same-second, same-side, 2+ prices) ===")
    print("  windows showing a swept ladder    %d / %d = %.1f%%"
          % (swept_windows, n, 100 * swept_windows / n))
    print("  sweep events                      %d" % swept_fills)
    dist("prices hit in ONE second", ladder_depths, "%.0f")
    dist("distinct price levels per side", levels_per_side, "%.0f")
    print()
    print("=== 3. CADENCE AND CLIP ===")
    dist("seconds between fills", gaps, "%.0f")
    dist("contracts per fill", sizes, "%.1f")
    dist("fill price", prices, "%.3f")
    print()
    print("=== 4. CAPITAL ACTUALLY AT RISK (not bankroll) ===")
    dist("peak unmatched contracts", peak_resid_contracts, "%.0f")
    dist("peak cash at risk, USD", peak_capital, "%.2f")

    out = dict(
        windows=n, fills=len(all_fill_s),
        first_fill_s={k: pct(first_fill_s, k / 100) for k in (10, 50, 90)},
        both_sides_live_s={k: pct(both_sides_s, k / 100) for k in (10, 50, 90)},
        swept_ladder_window_share=round(swept_windows / n, 4),
        ladder_prices_in_one_second={k: pct(ladder_depths, k / 100)
                                     for k in (10, 50, 90)},
        levels_per_side={k: pct(levels_per_side, k / 100) for k in (10, 50, 90)},
        gap_s={k: pct(gaps, k / 100) for k in (10, 50, 90)},
        contracts_per_fill={k: pct(sizes, k / 100) for k in (10, 50, 90)},
        peak_cash_at_risk_usd={k: pct(peak_capital, k / 100) for k in (10, 50, 90)},
        labels=("fill times are OBSERVED; submit times and queue positions are "
                "NOT in this data and are not claimed. a same-second two-price "
                "fill on one side cannot come from one order, which is the only "
                "direct evidence of a resting ladder available."),
    )
    with open("analysis/l5/fill_mechanics/mechanics.json", "w") as fh:
        json.dump(out, fh, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

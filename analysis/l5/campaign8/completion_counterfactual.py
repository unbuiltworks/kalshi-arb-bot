#!/usr/bin/env python3
"""Does completing a stranded leg beat letting it settle? Priced on SETTLED
outcomes across Campaigns 6, 7 and 8.

WHY THIS IS THE RIGHT TEST, and why "the complement later touched our price"
is not. A resting completion quote fills when the market moves TOWARD it --
which is exactly when the leg we already hold is becoming a winner. So
completion does not add a free pair: it SELLS the favourable tail of the
stranded leg for a locked pair edge. The counterfactual therefore has to be
priced against what the stranded legs actually settled for, not against pair
arithmetic, and not against a fill probability estimated from a tape where
cancellation is informative censoring.
"""
import statistics as st

#: every leg that reached window end unmatched, across C6/C7/C8.
#: own = per-contract own-side acquisition price; won = settled in our favour.
STRANDED = [
    # camp, window, side, qty,  own,    won
    ("C7", "2000z", "DOWN", 1.85, 0.3300, True),
    ("C7", "2015z", "UP",   2.18, 0.3700, False),
    ("C7", "2100z", "UP",   2.06, 0.4500, True),
    ("C7", "2115z", "UP",   1.85, 0.6200, False),
    ("C8", "0215z", "DOWN", 1.94, 0.4100, True),
    ("C8", "0230z", "UP",   1.02, 0.2100, False),
    ("C8", "0245z", "DOWN", 1.92, 0.3900, False),
]
# C6 stranded nothing: every window closed matched.

#: matched-pair economics per window, all three campaigns (zero-trade
#: windows included as 0.0 so the per-window mean is not flattered).
MATCHED = [
    ("C6", "1600z", 0.1818), ("C6", "1615z", 0.9045), ("C6", "1630z", 0.5045),
    ("C6", "1645z", 0.0),
    ("C7", "2000z", 0.0), ("C7", "2015z", 0.0), ("C7", "2030z", 0.0),
    ("C7", "2045z", 0.0), ("C7", "2100z", 0.1284), ("C7", "2115z", 0.2255),
    ("C7", "2130z", 0.3225),
    ("C8", "0200z", 0.3947), ("C8", "0215z", 0.0636), ("C8", "0230z", 0.0877),
    ("C8", "0245z", 0.0), ("C8", "0300z", 0.2184), ("C8", "0315z", 0.2540),
]


def main():
    print("=" * 74)
    print("STRANDED LEGS -- what they actually settled for")
    print("=" * 74)
    print("%-3s %-6s %-5s %6s %6s %8s %8s %10s" %
          ("", "win", "side", "qty", "own", "cost", "proceeds", "settled"))
    actual = []
    for c, w, s, q, own, won in STRANDED:
        cost = q * own
        proc = q if won else 0.0
        net = proc - cost
        actual.append(net)
        print("%-3s %-6s %-5s %6.2f %6.2f %8.4f %8.2f %+10.4f" %
              (c, w, s, q, own, cost, proc, net))
    tot_qty = sum(r[3] for r in STRANDED)
    tot_cost = sum(r[3] * r[4] for r in STRANDED)
    print("-" * 74)
    print("legs %d   qty %.2f   cost basis %.4f   won %d of %d" %
          (len(STRANDED), tot_qty, tot_cost,
           sum(1 for r in STRANDED if r[5]), len(STRANDED)))
    print("ACTUAL settled residual net  %+.4f   (mean/leg %+.4f, sd %.4f)" %
          (sum(actual), st.mean(actual), st.stdev(actual)))

    print()
    print("=" * 74)
    print("COUNTERFACTUAL: complete every one of them at pair cost c")
    print("=" * 74)
    print("%6s %12s %12s %10s" % ("c", "cf_net", "delta", "sd(cf)"))
    for c in (0.90, 0.92, 0.94, 0.9532, 0.96, 0.98, 1.00):
        cf = [q * (1.0 - c) for _, _, _, q, _, _ in STRANDED]
        print("%6.4f %+12.4f %+12.4f %10.4f" %
              (c, sum(cf), sum(cf) - sum(actual), st.stdev(cf)))
    be = 1.0 - sum(actual) / tot_qty
    print("\nBREAK-EVEN completion pair cost: %.4f" % be)
    print("  Completing beats stranding ONLY below this. Our achieved")
    print("  quantity-weighted pair cost in C8 was 0.9530 -- a dead heat.")
    print("  The expectation is a wash; what changes is the variance:")
    print("    stranding   sd %.4f per leg" % st.stdev(actual))
    print("    completing  sd %.4f per leg (at c=0.96)" %
          st.stdev([q * 0.04 for _, _, _, q, _, _ in STRANDED]))

    print()
    print("=" * 74)
    print("MATCHED-PAIR ECONOMICS -- where the edge actually is")
    print("=" * 74)
    v = [x[2] for x in MATCHED]
    m, sd = st.mean(v), st.stdev(v)
    se = sd / len(v) ** 0.5
    print("windows %d   total %+.4f   mean/window %+.4f   sd %.4f   se %.4f   t %.3f"
          % (len(v), sum(v), m, sd, se, m / se))
    res = [0.0] * len(MATCHED)
    for i, (c, w, _) in enumerate(MATCHED):
        for cc, ww, _, q, own, won in STRANDED:
            if cc == c and ww == w:
                res[i] = (q if won else 0.0) - q * own
    rm, rsd = st.mean(res), st.stdev(res)
    rse = rsd / len(res) ** 0.5
    print("residual: total %+.4f  mean/window %+.4f  sd %.4f  se %.4f  t %.3f"
          % (sum(res), rm, rsd, rse, rm / rse))
    comb = [a + b for a, b in zip(v, res)]
    cm, csd = st.mean(comb), st.stdev(comb)
    cse = csd / len(comb) ** 0.5
    print("combined: total %+.4f  mean/window %+.4f  sd %.4f  se %.4f  t %.3f"
          % (sum(comb), cm, csd, cse, cm / cse))
    print()
    print("  windows to reach t=2 on the COMBINED series:  %5.0f" %
          ((2 * csd / cm) ** 2))
    print("  windows to reach t=2 on the MATCHED series:   %5.0f" %
          ((2 * sd / m) ** 2))
    print("  -> killing residual variance is worth %.0fx in sample size." %
          (((2 * csd / cm) ** 2) / ((2 * sd / m) ** 2)))


if __name__ == "__main__":
    main()

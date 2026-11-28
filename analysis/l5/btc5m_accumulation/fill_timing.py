"""Does WHEN a straddle leg fills predict whether it wins? READ-ONLY.

THE HYPOTHESIS, pre-registered before looking, because the margin on the
straddle is 1.5 points and 80 arms have already been searched here.

A binary's price IS the market's probability estimate. Buying UP at 0.05
is a bet that 5% is too low. How wrong 5% can be depends entirely on how
much time is left:

  4 minutes left   plenty of room for BTC to come back. 0.05 may well
                   understate the true chance; the fill can be +EV.
  20 seconds left  the outcome is nearly fixed. The true chance is close
                   to 0, and 0.05 is a bad price for a near-certainty.

So the SAME price should be a good fill early and a terrible one late.
This matters because our breakeven is exactly the price paid: a leg bought
at x wins 1-x and loses x, so it needs a win rate above x. If late fills
sit below that line and early fills above it, the straddle has a lever
nobody has pulled: rest the ladder early, pull the deep rungs before the
close.

WHAT WOULD FALSIFY IT. A flat or rising win rate with time-in-window.
Note the NULL here is not "no effect" -- it is the risk-neutral
martingale, under which a fair price wins at exactly its own rate at
EVERY time, so the win-rate-minus-price line is flat at zero. A real
effect is a DOWNWARD SLOPE in that difference.

HOW THIS AVOIDS BEING ANOTHER SEARCH. The prediction is directional and
monotone, so the test is the trend across ordered buckets -- Spearman on
(fill time, win-minus-breakeven) -- not the best of several buckets. The
per-bucket figures are printed to be read, not to be picked from.

Usage: python3 -I analysis/l5/btc5m_accumulation/fill_timing.py
"""
from __future__ import annotations

import math
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.getcwd())
os.environ["L5_BTC5M_CONTROLLER"] = "ON"

from src import l5_btc5m_controller as C          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = open(os.path.join(HERE, "replay.py"), encoding="utf-8").read()
exec(compile(_SRC.replace("if __name__ ==", "if 0 and __name__ =="),
             "replay.py", "exec"))                # noqa: S102 - our own file

STEP = 0.5
#: the prices the live ladder actually rests at
PRICES = [D(c) / 100 for c in (5, 6, 7, 8, 9, 10)]


def legs(rows, x):
    """Every leg a resting buy at `x` on both sides would have taken,
    with the time it filled and whether it eventually won."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return []
    filled, t = {}, 0.0
    while t < WINDOW_S - CANCEL_LEAD_S:            # noqa: F821
        got = quote_at(rows, t)                    # noqa: F821
        if got is not None:
            q, _age = got
            for s in (C.UP, C.DOWN):
                if s in filled:
                    continue
                b = own_bid(q, s)                  # noqa: F821
                if b is not None and b <= x:
                    filled[s] = t
        t += STEP
    #: a leg is only a DIRECTIONAL bet when the other side never filled.
    #: A matched pair's outcome is irrelevant -- it pays 1.00 either way.
    if len(filled) != 1:
        return []
    s, ft = next(iter(filled.items()))
    return [{"price": x, "t": ft, "won": s == won}]


def wilson(k, n, z=1.96):
    """A proportion's interval that stays sane at k=0, which matters: a
    bucket with no winners is the whole question, not a missing datum."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0, 0.0

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx)
                    * sum((b - my) ** 2 for b in ry))
    rho = num / den if den else 0.0
    #: t on n-2 df, the usual approximation; n is small so read it loosely
    t = (rho * math.sqrt(n - 2) / math.sqrt(1 - rho * rho)
         if abs(rho) < 1 and n > 2 else 0.0)
    return rho, t


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    all_legs = []
    for slug in slugs:
        for x in PRICES:
            all_legs.extend(legs(w[slug], x))

    print("windows %d   one-sided straddle legs at 0.05-0.10" % len(slugs))
    print("a leg bought at x wins 1-x and loses x, so BREAKEVEN = x itself")
    print("the null is the martingale: win-rate MINUS price flat at zero")
    print()

    #: fixed, equal-width buckets over the window, named before looking
    edges = [0, 60, 120, 180, 240, 300]
    print("%-12s %6s %6s %9s %9s %16s" % (
        "fill time", "legs", "wins", "win rate", "breakeven", "win - breakeven"))
    print("-" * 70)
    xs, ys = [], []
    for lo, hi in zip(edges, edges[1:]):
        got = [g for g in all_legs if lo <= g["t"] < hi]
        if not got:
            print("%-12s %6d" % ("%ds-%ds" % (lo, hi), 0))
            continue
        n = len(got)
        k = sum(1 for g in got if g["won"])
        be = statistics.fmean([float(g["price"]) for g in got])
        rate = k / n
        d = rate - be
        lo_ci, hi_ci = wilson(k, n)
        print("%-12s %6d %6d %8.1f%% %8.1f%% %+13.1f pts   "
              "[%.1f%%, %.1f%%]" % ("%ds-%ds" % (lo, hi), n, k,
                                    100 * rate, 100 * be, 100 * d,
                                    100 * lo_ci, 100 * hi_ci))
        #: one point per LEG, so the trend is not driven by bucket counts
        for g in got:
            xs.append(g["t"])
            ys.append((1.0 if g["won"] else 0.0) - float(g["price"]))

    print()
    if len(xs) < 3:
        print("VERDICT  too few one-sided legs to say anything")
        return 0
    rho, t = spearman(xs, ys)
    pooled = statistics.fmean(ys)
    se = (statistics.stdev(ys) / math.sqrt(len(ys))) if len(ys) > 1 else 0.0
    print("pooled edge   %+.2f pts   se %.2f   t %+.2f   (n=%d legs)"
          % (100 * pooled, 100 * se, (pooled / se if se else 0.0), len(ys)))
    print("trend         rho %+.3f   t %+.2f" % (rho, t))
    print()
    #: the pre-registered direction is NEGATIVE rho: later is worse.
    if rho < 0 and abs(t) >= 2.0:
        print("VERDICT  SUPPORTED. Later fills are worse, as predicted.")
        print("         A time cutoff on the deep rungs is worth testing")
        print("         live -- it removes legs, it does not add risk.")
    elif rho > 0 and abs(t) >= 2.0:
        print("VERDICT  REVERSED. Later fills are BETTER, against the")
        print("         prediction. Do not cut the rungs late; this would")
        print("         have been exactly the wrong change to make.")
    else:
        print("VERDICT  NOT SUPPORTED. No usable trend (|t| < 2).")
        print("         The martingale null survives: at these prices the")
        print("         market's clock-handling is not exploitable here.")
        print("         Do NOT add a time cutoff on this evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

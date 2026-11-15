"""Where does the straddle's money actually come from? READ-ONLY.

`straddle.py` reported a NET figure per price. That hides the only thing
that matters, because the strategy is two different bets glued together:

  BOTH legs fill    a matched pair for 2x that pays 1.00. Locked +1-2x,
                    outcome-independent. This is the entire thesis.
  ONE leg fills     a naked directional buy at x. `fill_timing.py` just
                    measured these at -4.54 cents a leg, t -8.39, n 986.
  NEITHER           free.

So the straddle is positive only if the pair case pays for the one-sided
bleed. A net figure cannot tell you whether it does, and at a 1.5-point
margin the difference decides whether the ladder should exist at all.

This prints the two contributions SEPARATELY, per price, with the
frequency of each case and the pooled standard error, plus a chronological
out-of-sample split -- the in-sample best of several prices is exactly the
quantity that has misled us here before.

Usage: python3 -I analysis/l5/btc5m_accumulation/straddle_decompose.py
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
PRICES = [D(c) / 100 for c in (5, 6, 7, 8, 9, 10, 12, 15, 20)]


def walk(rows, x):
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
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
    if len(filled) == 2:
        return {"case": "BOTH", "pnl": float(D(1) - 2 * x)}
    if len(filled) == 1:
        s = next(iter(filled))
        return {"case": "ONE",
                "pnl": float((D(1) if s == won else D(0)) - x)}
    return {"case": "NONE", "pnl": 0.0}


def stats(v):
    if len(v) < 2:
        return (statistics.fmean(v) if v else 0.0), 0.0, 0.0
    m = statistics.fmean(v)
    se = statistics.stdev(v) / math.sqrt(len(v))
    return m, se, (m / se if se else 0.0)


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    cut = int(len(slugs) * 0.67)
    print("windows %d   fixed two-sided limits, ONE share a side" % len(slugs))
    print("per-window cents. BOTH is the thesis; ONE is what it must pay for")
    print()
    print("%-6s %7s %7s %7s %9s %9s %9s %8s %7s" % (
        "price", "both%", "one%", "none%", "BOTH c/w", "ONE c/w",
        "NET c/w", "se", "t"))
    print("-" * 78)
    rows_by_price = {}
    for x in PRICES:
        rs = {}
        for slug in slugs:
            r = walk(w[slug], x)
            if r is not None:
                rs[slug] = r
        if not rs:
            continue
        rows_by_price[x] = rs
        n = len(rs)
        both = [r for r in rs.values() if r["case"] == "BOTH"]
        one = [r for r in rs.values() if r["case"] == "ONE"]
        none = [r for r in rs.values() if r["case"] == "NONE"]
        #: contribution PER WINDOW, so the two columns add to NET
        c_both = sum(r["pnl"] for r in both) / n
        c_one = sum(r["pnl"] for r in one) / n
        m, se, t = stats([r["pnl"] for r in rs.values()])
        print("%-6s %6.1f%% %6.1f%% %6.1f%% %+8.2f %+8.2f %+8.2f %7.2f %+6.2f"
              % (x, 100 * len(both) / n, 100 * len(one) / n,
                 100 * len(none) / n, 100 * c_both, 100 * c_one,
                 100 * m, 100 * se, t))

    print()
    print("the pair case must out-earn the one-sided bleed. Where BOTH c/w")
    print("is smaller than |ONE c/w|, the strategy is paying to play.")
    print()
    print("chronological out-of-sample, 67/33 split")
    print("%-6s %12s %12s %10s" % ("price", "in-sample c/w", "OOS c/w", "agree?"))
    print("-" * 46)
    for x, rs in rows_by_price.items():
        mi, _, _ = stats([rs[s]["pnl"] for s in slugs[:cut] if s in rs])
        mo, _, to = stats([rs[s]["pnl"] for s in slugs[cut:] if s in rs])
        agree = "yes" if (mi > 0) == (mo > 0) else "NO"
        print("%-6s %+11.2f %+11.2f %10s" % (x, 100 * mi, 100 * mo, agree))

    print()
    best = max(rows_by_price, key=lambda x: stats(
        [r["pnl"] for r in rows_by_price[x].values()])[0])
    m, se, t = stats([r["pnl"] for r in rows_by_price[best].values()])
    n_arms = len(rows_by_price)
    #: the best of n noisy arms beats zero by about sqrt(2 ln n) se even
    #: when every arm is worthless. That is the bar, not |t| > 2.
    bar = math.sqrt(2 * math.log(n_arms)) if n_arms > 1 else 2.0
    print("best arm %s   net %+.2f c/w   t %+.2f" % (best, 100 * m, t))
    print("searched %d prices, so best-of-noise expects |t| ~ %.2f"
          % (n_arms, bar))
    if t > bar:
        print("VERDICT  the best arm clears its own search bar.")
    else:
        print("VERDICT  the best arm does NOT clear its search bar. On this")
        print("         tape the fixed straddle is not distinguishable from")
        print("         the best of several worthless arms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

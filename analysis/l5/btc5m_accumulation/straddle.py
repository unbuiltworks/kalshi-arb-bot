"""Rest FIXED limits on both sides, then allocate inventory. READ-ONLY.

THE IDEA. Instead of requoting at the touch, name a price X and leave a
resting buy on BOTH sides at X for the whole window. If both fill you own
a matched pair for exactly 2X, which pays 1.00 -- so the pair profit is
1 - 2X, locked by construction. Whatever is left unmatched is a cheap
directional position you then decide what to do with.

WHY THIS IS NOT two_sided.py. That requoted at the touch every step, so
the price drifted with the market and the realized pair came in at 1.0494
against the 0.99 the touch implied. A FIXED limit cannot drift: you either
get filled at X or you do not. Adverse selection does not vanish -- it
moves out of the PRICE and into WHICH LEGS FILL.

THE ARITHMETIC THAT DECIDES THE RANGE. A pair pays 1.00, so 2X < 1.00 is
required: each side must rest BELOW 0.50. Resting at 0.75 on both sides
costs 1.50 for something worth 1.00 -- a locked 50c loss, whatever
happens. The deeper you rest the more each pair earns and the rarer a
double fill becomes, because you need the price to swing through BOTH X
and 1-X inside one five-minute window.

THE THREE OUTCOMES, accounted separately:

  BOTH FILL   locked profit 1 - 2X, outcome-independent. The good case.
  ONE FILLS   a naked directional position bought at X, settling 1 or 0.
              Note X < 0.50 means you are buying the side the market was
              moving AWAY from, i.e. the underdog at the moment of fill.
  NEITHER     nothing happens. Free.

Usage: python3 -I analysis/l5/btc5m_accumulation/straddle.py
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
QTY = D("1")             # one share a side; scale is linear and irrelevant


def walk(rows, x):
    """Leave a resting buy at `x` on BOTH sides for the whole window."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    filled = {}
    t = 0.0
    while t < WINDOW_S - CANCEL_LEAD_S:            # noqa: F821
        got = quote_at(rows, t)                    # noqa: F821
        if got is None:
            t += STEP
            continue
        q, _age = got
        for s in (C.UP, C.DOWN):
            if s in filled:
                continue
            b = own_bid(q, s)                      # noqa: F821
            if b is not None and b <= x:
                filled[s] = t
        t += STEP
    n = len(filled)
    if n == 2:
        return {"case": "BOTH", "pnl": float(D("1") - 2 * x) * float(QTY)}
    if n == 1:
        s = next(iter(filled))
        return {"case": "ONE",
                "pnl": float((D("1") if s == won else D(0)) - x) * float(QTY),
                "won": s == won}
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
    print("windows %d   FIXED two-sided limits, 1 share a side" % len(slugs))
    print("a pair pays 1.00, so only X < 0.50 can ever be profitable")
    print()
    print("%-7s %8s %7s %7s %7s %10s %10s %10s %9s %6s"
          % ("X", "pair$", "both", "one", "none", "both$", "one$",
             "net$", "net/win", "t"))
    print("-" * 96)
    best = None
    for cents in range(25, 50, 2):
        x = D(cents) / 100
        rs = {}
        for s in slugs:
            r = walk(w[s], x)
            if r:
                rs[s] = r
        both = [r for r in rs.values() if r["case"] == "BOTH"]
        one = [r for r in rs.values() if r["case"] == "ONE"]
        none = [r for r in rs.values() if r["case"] == "NONE"]
        bp = sum(r["pnl"] for r in both)
        op = sum(r["pnl"] for r in one)
        nets = [r["pnl"] for r in rs.values()]
        m, se, t = stats(nets)
        print("%-7s %8.2f %7d %7d %7d %+10.3f %+10.3f %+10.3f %+9.4f %6.2f"
              % (x, float(1 - 2 * x), len(both), len(one), len(none),
                 bp, op, bp + op, m, t))
        if best is None or m > best[1]:
            best = (x, m, t, rs)
    print()
    # ---- WHICH leg do the single fills catch?
    print("=" * 96)
    print("WHEN ONLY ONE LEG FILLS, WHICH ONE IS IT?")
    print("=" * 96)
    print("%-7s %8s %8s %9s" % ("X", "singles", "winners", "win_rate"))
    print("-" * 36)
    for cents in (25, 33, 41, 49):
        x = D(cents) / 100
        one = [walk(w[s], x) for s in slugs]
        one = [r for r in one if r and r["case"] == "ONE"]
        wins = sum(1 for r in one if r.get("won"))
        print("%-7s %8d %8d %8.1f%%"
              % (x, len(one), wins,
                 100.0 * wins / len(one) if one else 0.0))
    print()
    print("  'both' = a locked pair at 2X. 'one' = a naked leg bought at X,")
    print("  which is the side the market was moving AWAY from.")
    print()
    x, m, t, rs = best
    mi = stats([rs[s]["pnl"] for s in slugs[:cut] if s in rs])
    mo = stats([rs[s]["pnl"] for s in slugs[cut:] if s in rs])
    print("=" * 96)
    print("VERDICT")
    print("=" * 96)
    print("  best X = %s   net %+0.4f a window   t %+0.2f" % (x, m, t))
    print("  in-sample %+0.4f (t %+0.2f)   OOS %+0.4f (t %+0.2f)"
          % (mi[0], mi[2], mo[0], mo[2]))
    stable = (mi[0] > 0) == (mo[0] > 0)
    print()
    if m > 0 and t > 2.0 and stable:
        print("  CREDIBLE. Positive at t %+0.2f with the sign holding across"
              % t)
        print("  the split. Needs an INDEPENDENT tape before anything is")
        print("  risked, because this one chose X.")
    else:
        print("  NOT ESTABLISHED: %+0.4f at t %+0.2f, sign %s."
              % (m, t, "stable" if stable else "UNSTABLE"))
        both_n = sum(1 for r in rs.values() if r["case"] == "BOTH")
        one_n = sum(1 for r in rs.values() if r["case"] == "ONE")
        print("  At the best X, %d windows double-filled and %d single-"
              % (both_n, one_n))
        print("  filled. The single fills are the problem: to fill at X <")
        print("  0.50 the market must come DOWN to you, so the leg you are")
        print("  left holding is the one that was losing when you bought")
        print("  it. The locked pair profit has to outrun that, and it")
        print("  does not.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

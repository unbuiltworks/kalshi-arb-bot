"""Rest deep, then COMPLETE high. READ-ONLY.

THE IDEA, and it is the owner's. Every straddle measurement so far rests
both sides at the SAME price and never adapts. But a matched pair does not
need to cost 16c -- it needs to cost less than 1.00. So once one leg has
filled at 8c, the other side can be bought much higher and the pair still
profits:

    first leg 0.08 + completion 0.30  =  pair 0.38  ->  +62c locked

and a 30c leg fills FAR more often than an 8c one, because it needs a much
smaller move. The symmetric 0.08/0.08 straddle demands a full round trip
through both deep tails inside five minutes, which happens in 10.2% of
windows. Completing at 0.30 only needs the price to come back to the
middle.

This is not a new mechanism -- it is the COMPLETE action the L5 operating
model already describes ("opposite-side COMPLETE is often delayed until
pricing improves"), and the controller already carries hedge_target_pair,
hard_pair_ceiling and completion_reserve for it. STRADDLE mode simply never
used any of it.

THE TRADE-OFF being measured. Completing higher fills more often and earns
less per pair. Completing at 0.92 fills almost always and earns nothing.
Somewhere between there should be an optimum, and the question is whether
it beats +3.81c a window.

HOW IT IS SIMULATED. Rest both sides at `entry`. The moment one side fills
at time t1, rest the OPPOSITE side at `comp` for the remainder of the
window (to the same cancel lead). A pair pays 1.00 whatever the outcome; an
unmatched leg settles 1 or 0 on the real outcome. Fill rule, cancel lead,
own-price convention and linear settlement are the live ones, as everywhere
else in this directory.

NOTE ON WHAT THIS CANNOT SEE. The completion is a MAKER buy, so it fills
only if the opposite own price reaches `comp` on its own. Crossing the
spread to complete would fill immediately but pays the ask and is taker --
disabled by policy, and separately measured as unprofitable.

Usage: python3 -I analysis/l5/btc5m_accumulation/straddle_complete.py
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
OTHER = {C.UP: C.DOWN, C.DOWN: C.UP}


def walk(rows, entry, comp):
    """Rest both at `entry`; on the first fill, rest the other at `comp`."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    first = None          # (side, t) of the leg that filled first
    second = False
    t = 0.0
    stop = WINDOW_S - CANCEL_LEAD_S                # noqa: F821
    while t < stop:
        got = quote_at(rows, t)                    # noqa: F821
        if got is not None:
            q, _age = got
            if first is None:
                #: both sides still resting at `entry`
                for s in (C.UP, C.DOWN):
                    b = own_bid(q, s)              # noqa: F821
                    if b is not None and b <= entry:
                        first = (s, t)
                        break
            elif not second:
                #: the opposite side is now resting at `comp`
                o = OTHER[first[0]]
                b = own_bid(q, o)                  # noqa: F821
                if b is not None and b <= comp:
                    second = True
        t += STEP

    if first is None:
        return {"case": "NONE", "pnl": 0.0}
    if second:
        #: one of the two legs pays 1.00, whichever way it settles
        return {"case": "PAIR", "pnl": float(D(1) - entry - comp),
                "pair_cost": float(entry + comp)}
    s = first[0]
    return {"case": "LONE",
            "pnl": float((D(1) if s == won else D(0)) - entry)}


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
    entry = D("0.08")
    print("windows %d   entry 0.08 both sides, then COMPLETE the other side"
          % len(slugs))
    print("a pair pays 1.00, so any pair cost under 1.00 is locked profit")
    print()
    print("%-7s %8s %8s %8s %10s %9s %7s %6s %9s %9s %5s" % (
        "comp", "pair%", "lone%", "none%", "pair cost", "NET c/w", "se", "t",
        "in-samp", "OOS", "sign"))
    print("-" * 100)
    best = None
    for cents in (8, 12, 16, 20, 25, 30, 35, 40, 50, 60, 70, 80, 88, 92):
        comp = D(cents) / 100
        rs = [r for r in (walk(w[s], entry, comp) for s in slugs) if r]
        if not rs:
            continue
        n = len(rs)
        pair = [r for r in rs if r["case"] == "PAIR"]
        lone = [r for r in rs if r["case"] == "LONE"]
        none = [r for r in rs if r["case"] == "NONE"]
        m, se, t = stats([r["pnl"] for r in rs])
        mi, _, _ = stats([r["pnl"] for r in rs[:cut]])
        mo, _, _ = stats([r["pnl"] for r in rs[cut:]])
        mark = ""
        if best is None or m > best[1]:
            best = (comp, m, se, t)
            mark = ""
        print("%-7s %7.1f%% %7.1f%% %7.1f%% %10s %+9.2f %7.2f %+6.2f "
              "%+9.2f %+9.2f %5s%s" % (
                  comp, 100 * len(pair) / n, 100 * len(lone) / n,
                  100 * len(none) / n, str(entry + comp), 100 * m,
                  100 * se, t, 100 * mi, 100 * mo,
                  "ok" if (mi > 0) == (mo > 0) else "NO", mark))

    print()
    print("REFERENCE  symmetric 0.08/0.08: pair 10.2%%, net +3.81 c/w, t +1.67")
    print()
    if best is not None:
        comp, m, se, t = best
        print("best completion price %s -> %+.2f c/w, se %.2f, t %+.2f"
              % (comp, 100 * m, 100 * se, t))
        n_arms = 14
        bar = math.sqrt(2 * math.log(n_arms))
        print("searched %d completion prices, so best-of-noise expects "
              "|t| ~ %.2f" % (n_arms, bar))
        if t > bar:
            print("VERDICT  clears its own search bar. Worth implementing.")
        else:
            print("VERDICT  does NOT clear its search bar on its own -- but")
            print("         compare the SHAPE against the symmetric case")
            print("         before dismissing it: a higher pair rate at a")
            print("         similar net is a different risk profile, not")
            print("         just a different number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

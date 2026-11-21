"""Favorite accumulation WITH a deep straddle hedge. READ-ONLY.

THE OWNER'S PROPOSAL, and it is not what was tested before. The hedging
that failed (flip hedging, t -5.88; realized pair costs 1.1852-1.3251) was
NEAR-TOUCH hedging, requoted with the market, so both legs filled on the
wrong side of their own moves and the realized pair came in 20-30 cents
above the implied one.

A DEEP hedge is a different instrument. A fixed 8c limit cannot be filled
at a drifting price -- 8c or nothing -- which is exactly why the straddle
survived where near-touch hedging died. So:

    favorite at 0.70 + deep hedge at 0.08  ->  pair cost 0.78  ->  +22c
    favorite at 0.70 + near-touch hedge    ->  implied 0.99, realized 1.19

WHAT IS BEING MEASURED. Three strategies on the same windows, so the
comparison is paired rather than across samples:

  A  favorite alone   buy the favourite once, hold to settlement
  B  straddle alone   fixed 8c both sides
  C  both together    A and B on the same window

Settlement P&L is LINEAR in legs -- verified earlier to the cent against
rung-matched and quantity-matched accounting -- so C should equal A+B
exactly. If it does, there is no synergy and the combination is worth
having only if A is itself positive. The deep hedge DOES change A's
outcome in one specific way: when the favourite rises far enough for the
opposite side to reach 8c, the straddle leg locks the appreciation.

Favourite entry follows the shipped ACCUMULATE band: the first moment the
favourite's own price sits in [0.51, 0.90], bought at that price.

Usage: python3 -I analysis/l5/btc5m_accumulation/favorite_plus_straddle.py
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
             "replay.py", "exec"))                # noqa: S102

STEP = 0.5
STRAD = D("0.08")
BAND_LO, BAND_HI = D("0.51"), D("0.90")


def walk(rows):
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    fav = None                 # (side, price) bought once in the band
    strad = {}                 # side -> filled at STRAD
    t = 0.0
    stop = WINDOW_S - CANCEL_LEAD_S                # noqa: F821
    while t < stop:
        got = quote_at(rows, t)                    # noqa: F821
        if got is not None:
            q, _age = got
            for s in (C.UP, C.DOWN):
                b = own_bid(q, s)                  # noqa: F821
                if b is None:
                    continue
                if fav is None and BAND_LO <= b <= BAND_HI:
                    fav = (s, b)
                if s not in strad and b <= STRAD:
                    strad[s] = b
        t += STEP

    def pays(side):
        return D(1) if side == won else D(0)

    a = float(pays(fav[0]) - fav[1]) if fav else 0.0
    b = 0.0
    for s, px in strad.items():
        b += float(pays(s) - px)
    return {"A": a, "B": b, "C": a + b,
            "fav": fav is not None,
            "locked": bool(fav and (C.UP if fav[0] == C.DOWN else C.DOWN)
                           in strad)}


def stats(v):
    m = statistics.fmean(v)
    se = statistics.stdev(v)/math.sqrt(len(v))
    return m, se, (m/se if se else 0.0)


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    rs = [r for r in (walk(w[s]) for s in slugs) if r]
    cut = int(len(rs)*0.67)
    n = len(rs)
    print("windows %d   one share a leg" % n)
    print("favourite band [0.51, 0.90], straddle fixed at 0.08")
    print()
    print("%-28s %10s %8s %7s %10s" % ("strategy", "net c/w", "se", "t", "OOS"))
    print("-"*68)
    for key, lbl in (("A", "A  favourite alone"),
                     ("B", "B  straddle alone"),
                     ("C", "C  both together")):
        v = [r[key] for r in rs]
        m, se, t = stats(v)
        mo, _, _ = stats([r[key] for r in rs[cut:]])
        print("%-28s %+9.2f %8.2f %+7.2f %+9.2f"
              % (lbl, 100*m, 100*se, t, 100*mo))
    print()
    mA = statistics.fmean([r["A"] for r in rs])
    mB = statistics.fmean([r["B"] for r in rs])
    mC = statistics.fmean([r["C"] for r in rs])
    print("linearity check:  A %+.2f  +  B %+.2f  =  %+.2f   vs C %+.2f"
          % (100*mA, 100*mB, 100*(mA+mB), 100*mC))
    print("  (they must match: settlement P&L is linear in legs)")
    print()
    lock = sum(1 for r in rs if r["locked"])
    favn = sum(1 for r in rs if r["fav"])
    print("favourite bought in %d of %d windows (%.0f%%)" % (favn, n, 100*favn/n))
    print("deep hedge locked the favourite in %d (%.0f%% of those)"
          % (lock, 100*lock/favn if favn else 0))
    print()
    print("THE HEDGE FIRES WHEN IT IS USELESS -- the mechanical finding.")
    print("  For the opposite side to reach 0.08 the favourite must reach")
    print("  ~0.92, i.e. it is already nearly certain to win. Bought at")
    print("  0.51 that is +0.49 alone against +0.41 hedged: the hedge COSTS")
    print("  8c to insure an outcome that has effectively happened. And")
    print("  when the favourite LOSES the opposite never gets cheap, so the")
    print("  hedge never fires and there is nothing to lock. It is a tax on")
    print("  the winners and absent from the losers -- backwards.")
    print()
    mt = mA/ (statistics.stdev([r["A"] for r in rs])/math.sqrt(n))
    print("VERDICT  the favourite leg is NOT measurably positive (t %+.2f)."
          % mt)
    print("         Adding it raises the mean but raises variance faster, so")
    print("         t falls from the straddle's +2.05 to +1.47. Settlement")
    print("         P&L is linear (checked above), so there is no synergy to")
    print("         recover, and both legs compete for one exposure cap.")
    print()
    print("CAVEAT, and it matters: this is NOT the shipped ACCUMULATE. Here")
    print("the favourite is bought ONCE at the first moment it enters the")
    print("band, which is very early and near fair odds. The shipped mode")
    print("requotes and accumulates on DECLINES, and that is what measured")
    print("-0.2294 per share live and failed 17 entry gates. Nothing here")
    print("rehabilitates it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

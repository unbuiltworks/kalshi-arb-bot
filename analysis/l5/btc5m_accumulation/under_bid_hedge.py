"""Rest 2c UNDER the bid in the favourite band, then hedge. READ-ONLY.

THE OWNER'S SPEC: rest orders in the favourite band [0.51, 0.90] at two
cents below the bid, and when they fill, hedge the unfavoured side.

WHY THIS IS NOT THE EARLIER TEST. favorite_plus_straddle.py bought the
favourite AT its own price the first moment it entered the band -- no
patience, no discount. Resting two cents UNDER means you only fill on a
DIP, and you get a 2c better price when you do. That is also what the
shipped EDGE_GATED mode did; what killed EDGE_GATED was its edge gate
(it needed ~7c under the bid on a 1c book, arithmetically unreachable),
not the resting itself. So the resting rule deserves a test with the gate
removed.

THE REQUOTING QUESTION, which is the crux. "Two cents below the bid" is an
ongoing instruction: as the bid moves, the order follows. That is exactly
what makes the fill price DRIFT, and drift is what produced realized pair
costs of 1.1852-1.3251 on this branch. So both are measured:

  FOLLOW   requote to bid-0.02 every step (the owner's literal spec)
  ANCHOR   place once at bid-0.02 and leave it (no drift possible)

If FOLLOW loses and ANCHOR does not, drift is the mechanism and the spec
needs the anchor variant. If both lose, the band itself is the problem.

HEDGING. On a fill at F, rest the unfavoured side at H and sweep H. A pair
pays 1.00, so it profits whenever F + H < 1.00. `touch` hedges at whatever
the unfavoured side costs at that moment, which is the near-touch hedging
already measured at t -5.88 -- included so the comparison is on the same
windows rather than across studies.

Usage: python3 -I analysis/l5/btc5m_accumulation/under_bid_hedge.py
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
UNDER = D("0.02")
BAND_LO, BAND_HI = D("0.51"), D("0.90")
OTHER = {C.UP: C.DOWN, C.DOWN: C.UP}


def walk(rows, follow, hedge, under=UNDER):
    """hedge: None | 'touch' | a Decimal price on the unfavoured side."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    resting = None          # (side, price) our live order
    fill = None             # (side, price) what we bought
    hedged = None           # price paid on the other side
    t = 0.0
    stop = WINDOW_S - CANCEL_LEAD_S                # noqa: F821
    while t < stop:
        got = quote_at(rows, t)                    # noqa: F821
        if got is not None:
            q, _age = got
            px = {s: own_bid(q, s) for s in (C.UP, C.DOWN)}   # noqa: F821
            if px[C.UP] is not None and px[C.DOWN] is not None:
                if fill is None:
                    #: CHECK THE FILL FIRST, against the order placed from
                    #: the PREVIOUS observation. Requoting and then testing
                    #: the same quote makes the order sit below the market
                    #: by construction and it can never fill -- that is an
                    #: artifact of the loop order, not a property of the
                    #: strategy, and it showed as a flat 0% fill rate.
                    if resting is not None:
                        s, rp = resting
                        if px[s] is not None and px[s] <= rp:
                            fill = (s, rp)
                            resting = None
                    if fill is None:
                        fav = C.UP if px[C.UP] >= px[C.DOWN] else C.DOWN
                        b = px[fav]
                        if BAND_LO <= b <= BAND_HI:
                            want = b - under
                            if resting is None or follow:
                                resting = (fav, want)
                        elif follow:
                            resting = None  # band exited; pull the order
                elif hedge is not None and hedged is None:
                    o = OTHER[fill[0]]
                    hp = px[o]
                    if hp is None:
                        pass
                    elif hedge == "touch":
                        hedged = hp
                    elif hp <= hedge:
                        hedged = hedge
        t += STEP

    if fill is None:
        return {"pnl": 0.0, "filled": 0, "hedged": 0, "pair": None}

    def pays(side):
        return D(1) if side == won else D(0)

    pnl = pays(fill[0]) - fill[1]
    pair = None
    if hedged is not None:
        o = OTHER[fill[0]]
        pnl += pays(o) - hedged
        pair = float(fill[1] + hedged)
    return {"pnl": float(pnl), "filled": 1,
            "hedged": int(hedged is not None), "pair": pair}


def stats(v):
    m = statistics.fmean(v)
    se = statistics.stdev(v)/math.sqrt(len(v))
    return m, se, (m/se if se else 0.0)


def run(w, slugs, cut, follow, label):
    print("%s" % label)
    print("  %-7s %-8s %6s %7s %10s %9s %7s %6s %9s" % (
        "under", "hedge", "fill%", "hedge%", "pair cost", "net c/w",
        "se", "t", "OOS"))
    print("  " + "-"*78)
    for ucents in (2, 3, 5, 8, 12, 15):
        under = D(ucents)/100
        for hedge, hl in ((None, "none"), ("touch", "touch"),
                          (D("0.08"), "0.08")):
            rs = [r for r in (walk(w[s], follow, hedge, under)
                              for s in slugs) if r]
            n = len(rs)
            m, se, t = stats([r["pnl"] for r in rs])
            mo, _, _ = stats([r["pnl"] for r in rs[cut:]])
            nf = sum(r["filled"] for r in rs)
            nh = sum(r["hedged"] for r in rs)
            pairs = [r["pair"] for r in rs if r["pair"] is not None]
            pc = ("%.4f" % statistics.median(pairs)) if pairs else "-"
            print("  %-7s %-8s %5.0f%% %6.0f%% %10s %+9.2f %7.2f %+6.2f %+9.2f"
                  % ("%dc" % ucents, hl, 100*nf/n, 100*nh/n, pc,
                     100*m, 100*se, t, 100*mo))
        print()


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = [s for s in sorted(w) if outcome(w[s]) is not None]  # noqa: F821
    cut = int(len(slugs)*0.67)
    print("windows %d   band [0.51, 0.90], resting 2c under the bid" % len(slugs))
    print()
    run(w, slugs, cut, False, "ANCHOR -- placed once at bid-N, never moved")
    print("reference on these windows: straddle alone +4.66 c/w, t +2.05")
    print("a pair costing MORE than 1.0000 is a locked loss by construction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

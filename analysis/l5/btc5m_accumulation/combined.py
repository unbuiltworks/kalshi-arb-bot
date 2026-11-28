"""Favorite accumulation PLUS the deep straddle, together. READ-ONLY.

THE HOPE. Hold the favorite at ~0.70. Leave a deep resting bid at X on the
other side. When the window resolves your way the loser collapses through
X, so that bid fills, and you own a pair for 0.70 + X -- well under the
1.00 it pays. The cheap leg appears to pay for the expensive one.

THE SUSPICION. The cheap leg only fills when the loser collapses, which is
exactly when the favorite you already hold was going to WIN. It is
anti-correlated with need: a tax on your winning windows rather than
insurance on your losing ones. When the favorite loses, the other side
climbs away and never reaches X, so no hedge arrives at all -- and worse,
your OWN side collapses through X and the deep bid buys MORE of the loser.

THREE ARMS on one tape, sharing inventory so pairs net properly:

  FAV    favorite accumulation at the touch, band 0.51-0.90 (shipped)
  DEEP   fixed limits at X on both sides (straddle.py)
  BOTH   both at once, one book of inventory

If the arms were independent bets, BOTH would equal FAV + DEEP exactly.
The gap between BOTH and that sum IS the interaction, and its sign is the
whole question.

Usage: python3 -I analysis/l5/btc5m_accumulation/combined.py
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
DEEP_X = D("0.08")
CLIP = D("0.10")
MAX_RESTING = 4
BAND_LO, BAND_HI = D("0.51"), D("0.90")


def favourite(q):
    bid, ask = dec(q["bid"]), dec(q["ask"])        # noqa: F821
    if bid is None or ask is None:
        return None
    up, dn = bid, D("1") - ask
    return (C.UP, up) if up >= dn else (C.DOWN, dn)


def walk(rows, do_fav, do_deep):
    """Returns the filled legs as (side, price, qty) across both books."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    legs = []
    rest_f = {}
    deep_done = set()
    n_f = 0
    t = 0.0
    while t < WINDOW_S - CANCEL_LEAD_S:            # noqa: F821
        got = quote_at(rows, t)                    # noqa: F821
        if got is None:
            t += STEP
            continue
        q, _age = got
        f = favourite(q)
        if f is None:
            t += STEP
            continue
        side, touch = f

        for s in (C.UP, C.DOWN):
            b = own_bid(q, s)                      # noqa: F821
            if b is None:
                continue
            p = rest_f.get(s)
            if p is not None and b < p:            # favorite leg fills
                legs.append((s, p, CLIP / p))
                del rest_f[s]
                n_f += 1
            if do_deep and s not in deep_done and b <= DEEP_X:
                legs.append((s, DEEP_X, CLIP / DEEP_X))
                deep_done.add(s)

        if do_fav and BAND_LO <= touch <= BAND_HI and n_f < MAX_RESTING:
            for s in list(rest_f):
                if s != side:
                    del rest_f[s]
            rest_f[side] = touch
        elif do_fav:
            rest_f.clear()
        t += STEP

    net = float(sum((D("1") if s == won else D(0)) * qty - p * qty
                    for s, p, qty in legs))
    return {"net": net, "legs": len(legs)}


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
    arms = {"FAV  (shipped)": (True, False),
            "DEEP (X=%s)" % DEEP_X: (False, True),
            "BOTH": (True, True)}
    runs = {k: {} for k in arms}
    for s in slugs:
        for k, (a, b) in arms.items():
            r = walk(w[s], a, b)
            if r:
                runs[k][s] = r

    print("windows %d   clip $%s a leg, deep X=%s, band %s-%s"
          % (len(slugs), CLIP, DEEP_X, BAND_LO, BAND_HI))
    for bname, blk in (("ALL", slugs), ("IN-SAMPLE", slugs[:cut]),
                       ("OUT-OF-SAMPLE", slugs[cut:])):
        print()
        print("=" * 76)
        print("%s   %d windows" % (bname, len(blk)))
        print("=" * 76)
        print("%-18s %7s %10s %9s %6s" % ("arm", "legs", "net/win", "se", "t"))
        print("-" * 76)
        print("%-18s %7d %+10.4f %9.4f %6.2f" % ("NO-TRADE", 0, 0, 0, 0))
        for k in arms:
            sub = [runs[k][s] for s in blk if s in runs[k]]
            m, se, t = stats([r["net"] for r in sub])
            print("%-18s %7d %+10.4f %9.4f %+6.2f"
                  % (k, sum(r["legs"] for r in sub), m, se, t))

    fav = runs["FAV  (shipped)"]
    deep = runs["DEEP (X=%s)" % DEEP_X]
    both = runs["BOTH"]
    common = [s for s in slugs if s in fav and s in deep and s in both]
    inter = [both[s]["net"] - (fav[s]["net"] + deep[s]["net"])
             for s in common]
    mi, sei, ti = stats(inter)
    print()
    print("=" * 76)
    print("INTERACTION: BOTH minus (FAV + DEEP), same windows")
    print("=" * 76)
    if abs(mi) < 1e-9:
        print("  %+0.4f a window over n %d -- ZERO to numerical precision."
              % (mi, len(inter)))
        print("  (A t-statistic on a mean of 0 with a se of 0 is float")
        print("  noise and is not reported.)")
    else:
        print("  %+0.4f a window   se %.4f   t %+0.2f   n %d"
              % (mi, sei, ti, len(inter)))
    print()
    mb = stats([both[s]["net"] for s in common])
    mf = stats([fav[s]["net"] for s in common])
    md = stats([deep[s]["net"] for s in common])
    print("  FAV  %+0.4f (t %+0.2f)" % (mf[0], mf[2]))
    print("  DEEP %+0.4f (t %+0.2f)" % (md[0], md[2]))
    print("  BOTH %+0.4f (t %+0.2f)" % (mb[0], mb[2]))
    print()
    print("=" * 76)
    print("VERDICT")
    print("=" * 76)
    if abs(mi) < 1e-9:
        print("  NO INTERACTION, and it is not a coincidence of this tape.")
        print("  SETTLEMENT P&L IS LINEAR IN LEGS: each leg pays 1 or 0 on")
        print("  its own, so any basket of positions is exactly the sum of")
        print("  its legs. There is no pairing synergy to harvest -- a")
        print("  'pair' is a bookkeeping view, not an economic one. Any two")
        print("  strategies combined will ALWAYS be exactly additive here.")
    elif mi < 0:
        print("  THE COMBINATION IS WORSE than running them apart, by")
        print("  %+0.4f a window. The cheap leg fills when the loser" % mi)
        print("  collapses -- which is exactly when the favorite you hold")
        print("  was already going to win. It taxes the winning windows.")
    else:
        print("  THE COMBINATION ADDS %+0.4f a window beyond the sum." % mi)
    print()
    if mb[0] < md[0]:
        print("  AND THE DECIDING POINT: BOTH (%+0.4f) is WORSE than DEEP"
              % mb[0])
        print("  alone (%+0.4f). Adding a losing strategy to a better one"
              % md[0])
        print("  never helps -- it dilutes it by exactly its own loss.")
        print("  Combining does not make favorite accumulation work; it")
        print("  makes the other thing work less well.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

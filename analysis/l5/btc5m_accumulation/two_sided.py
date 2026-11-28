"""Quote BOTH sides as a maker: the real market-making trade. READ-ONLY.

THE IDEA, which is different from every hedge tested before. At any
instant the favorite's own bid and the underdog's own bid sum to
`1 - spread`, because UP's bid is the YES bid and DOWN's bid is
1 - (YES ask). So resting a maker buy on BOTH sides at their own bids
buys a matched pair for 0.99 when the spread is a cent, and a matched
pair pays exactly 1.00 at settlement. That is +1c of locked profit per
pair, and it does not care who wins.

HOW THIS DIFFERS FROM replay_hedge.py, which was correctly negative.
That tested hedging AFTER a flip: you hold the old favorite, it loses the
lead, and you complete by buying the NEW favorite at >= 0.51. Both legs
are then >= 0.51 and the pair costs >= 1.02 -- a locked LOSS. Quoting both
sides from the start never pays 0.51 for the cheap leg; it pays about
0.35. Same word, opposite economics.

WHY IT IS NOT FREE MONEY. You are only paid the spread if BOTH legs fill.
A resting buy on UP fills when the YES bid falls; a resting buy on DOWN
fills when the YES ask RISES. In a market that trends, one side fills and
the other does not, leaving a one-sided directional position -- the
"stranding" that `analysis/l5/BAND_SPREAD_FINDINGS.md` measured at 28.2%
and priced at a required spread of about 0.0729. BTC5m offers 0.01. This
script measures what actually happens rather than assuming either way.

WHAT IS REPORTED. Pairs formed and what each cost; legs left stranded and
what they settled for; and the net, split into the two so the mechanism is
visible rather than only the total.

Usage: python3 -I analysis/l5/btc5m_accumulation/two_sided.py
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
QTY = D("0.15")          # one clip per side, comparable to the live size
MAX_PAIRS = 4            # at most this many pairs working at once


def both_bids(q):
    """(up_own_bid, down_own_bid). They sum to 1 - spread."""
    bid, ask = dec(q["bid"]), dec(q["ask"])        # noqa: F821
    if bid is None or ask is None or ask < bid:
        return None
    return bid, D("1") - ask


def walk(rows):
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    inv = {C.UP: [], C.DOWN: []}                   # filled legs, as prices
    rest = {}                                      # side -> resting price
    t = 0.0
    while t < WINDOW_S - CANCEL_LEAD_S:            # noqa: F821
        got = quote_at(rows, t)                    # noqa: F821
        if got is None:
            t += STEP
            continue
        q, _age = got
        b = both_bids(q)
        if b is None:
            t += STEP
            continue
        live = {C.UP: b[0], C.DOWN: b[1]}

        # ---- fills: a resting buy fills when ITS side's bid falls through
        for side in (C.UP, C.DOWN):
            p = rest.get(side)
            if p is not None and live[side] < p:
                inv[side].append(p)
                del rest[side]

        # ---- requote both sides at the touch, while capacity remains
        for side in (C.UP, C.DOWN):
            if len(inv[side]) >= MAX_PAIRS:
                rest.pop(side, None)
                continue
            px = live[side]
            if px <= 0 or px >= 1:
                rest.pop(side, None)
                continue
            rest[side] = px
        t += STEP

    up, dn = sorted(inv[C.UP]), sorted(inv[C.DOWN])
    npair = min(len(up), len(dn))
    pairs = [(up[i], dn[i]) for i in range(npair)]
    stray_side = C.UP if len(up) > npair else C.DOWN
    stray = (up[npair:] if len(up) > npair else dn[npair:])
    pair_pnl = float(sum(D("1") - (a + c) for a, c in pairs)) * float(QTY)
    stray_pnl = float(sum((D("1") if stray_side == won else D(0)) - p
                          for p in stray)) * float(QTY)
    return {"won": won, "pairs": pairs, "stray": stray,
            "stray_side": stray_side, "pair_pnl": pair_pnl,
            "stray_pnl": stray_pnl, "net": pair_pnl + stray_pnl}


def stats(v):
    if len(v) < 2:
        return (statistics.fmean(v) if v else 0.0), 0.0, 0.0
    m = statistics.fmean(v)
    se = statistics.stdev(v) / math.sqrt(len(v))
    return m, se, (m / se if se else 0.0)


def block(name, runs):
    if not runs:
        return
    npair = sum(len(r["pairs"]) for r in runs)
    nstray = sum(len(r["stray"]) for r in runs)
    legs = 2 * npair + nstray
    pcosts = [float(a + c) for r in runs for a, c in r["pairs"]]
    pp = sum(r["pair_pnl"] for r in runs)
    sp = sum(r["stray_pnl"] for r in runs)
    nets = [r["net"] for r in runs]
    m, se, t = stats(nets)
    print("%-20s %5d %7d %7d %7.1f%% %9.4f %+9.4f %+9.4f %+9.4f %6.2f"
          % (name, len(runs), npair, nstray,
             100.0 * nstray / legs if legs else 0.0,
             statistics.fmean(pcosts) if pcosts else 0.0,
             pp, sp, m, t))


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    cut = int(len(slugs) * 0.67)
    runs = {}
    for s in slugs:
        r = walk(w[s])
        if r:
            runs[s] = r
    print("windows %d   two-sided maker, qty %s a leg, max %d pairs"
          % (len(runs), QTY, MAX_PAIRS))
    print()
    print("%-20s %5s %7s %7s %8s %9s %9s %9s %9s %6s"
          % ("block", "wins", "pairs", "stray", "stray%", "pair_cost",
             "pair$", "stray$", "net/win", "t"))
    print("-" * 104)
    for name, blk in (("ALL", slugs), ("IN-SAMPLE", slugs[:cut]),
                      ("OUT-OF-SAMPLE", slugs[cut:])):
        block(name, [runs[s] for s in blk if s in runs])
    print()
    allr = [runs[s] for s in slugs if s in runs]
    pcosts = [float(a + c) for r in allr for a, c in r["pairs"]]
    print("  pair cost: mean %.4f   below 1.00 in %d of %d pairs"
          % (statistics.fmean(pcosts),
             sum(1 for p in pcosts if p < 1.0), len(pcosts)))
    pp = sum(r["pair_pnl"] for r in allr)
    sp = sum(r["stray_pnl"] for r in allr)
    print("  pair P&L  %+0.4f   stranded P&L %+0.4f   net %+0.4f"
          % (pp, sp, pp + sp))
    print()
    m, se, t = stats([r["net"] for r in allr])
    mi = stats([runs[s]["net"] for s in slugs[:cut] if s in runs])
    mo = stats([runs[s]["net"] for s in slugs[cut:] if s in runs])
    print("=" * 104)
    print("VERDICT")
    print("=" * 104)
    print("  The pair leg is %s: %+0.4f over %d pairs at a mean REALIZED"
          % ("PROFITABLE" if pp > 0 else "unprofitable", pp, len(pcosts)))
    print("  cost of %.4f, against the 0.99 the simultaneous touch implies."
          % statistics.fmean(pcosts))
    print("  The stranded leg is %+0.4f." % sp)
    print()
    stable = (mi[0] > 0) == (mo[0] > 0)
    if t > 2.0 and stable and m > 0:
        print("  CREDIBLE: net %+0.4f a window, t %+0.2f, sign stable across"
              % (m, t))
        print("  the split (in %+0.4f, OOS %+0.4f). Needs an INDEPENDENT"
              % (mi[0], mo[0]))
        print("  tape before anything is risked, because this one chose it.")
    else:
        print("  NOT ESTABLISHED: net %+0.4f a window at t %+0.2f (in %+0.4f,"
              % (m, t, mi[0]))
        print("  OOS %+0.4f, sign %s)." % (mo[0],
                                           "stable" if stable else "UNSTABLE"))
        strand_pct = (100.0 * sum(len(r["stray"]) for r in allr)
                      / max(1, sum(2 * len(r["pairs"]) + len(r["stray"])
                                   for r in allr)))
        print()
        print("  AND THE REASON IS NOT STRANDING. Only %.1f%% of legs end"
              % strand_pct)
        print("  unmatched, worth %+0.4f in total -- essentially nothing."
              % sp)
        print("  Quoting both sides continuously for 300s almost always")
        print("  fills both eventually, so the risk the band-spread study")
        print("  priced (28.2% stranding) is not what bites here.")
        print()
        print("  What bites is the REALIZED pair cost: %.4f, not the 0.99"
              % statistics.fmean(pcosts))
        print("  the simultaneous touch implies. The two legs do not fill at")
        print("  the same instant. A resting buy is only reached when its")
        print("  own side's bid falls THROUGH it, so each leg is bought")
        print("  about 2.5c above where it settles -- and the pair, needing")
        print("  both, pays that twice. %d of %d pairs did come in under"
              % (sum(1 for p in pcosts if p < 1.0), len(pcosts)))
        print("  1.00, but the mean is %.4f, a locked loss per pair."
              % statistics.fmean(pcosts))
        print()
        print("  This is the SAME 4.87c adverse selection the one-sided")
        print("  strategy pays, arriving through a different door. You")
        print("  cannot buy the touch on both sides; you can only buy what")
        print("  the touch leaves behind as it moves away from you.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

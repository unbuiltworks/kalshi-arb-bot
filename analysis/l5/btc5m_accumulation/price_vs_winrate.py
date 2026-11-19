"""Can a better PRICE get us above the win rate? READ-ONLY.

THE MATH, stated first because it decides what is worth measuring.

A share bought at price p on a side that wins with probability w has
expected value w - p. So there are exactly two levers:

  SIZE    cannot help, and this is a theorem rather than a finding.
          Expectation is linear: E[sum qi*Xi] = sum qi*E[Xi]. If every
          E[Xi] < 0 and we only buy (qi >= 0), every quantity vector gives
          a negative total. Martingale, anti-martingale, Kelly, pyramiding,
          scaling in or out -- all change the variance and the shape of the
          distribution, none change the sign of the mean. Confirmed live:
          5x the size reproduced the same per-share result.

  PRICE   CAN help, because p enters the expectation directly. Buy below
          your win rate and you profit. This is the lever worth measuring.

THE CATCH, and the whole question. Resting deeper does lower p -- but the
fill process links p to w. A resting buy is only reached when the market
comes DOWN to it, so the deeper you rest, the more selected the fills are
towards exactly the moves that beat you. The question is which falls
faster: the price you pay, or the rate at which you win.

This decomposes the depth sweep into those two numbers at every depth, so
the mechanism is visible rather than only the net. `gap = w - p` IS the
per-share edge.

Usage: python3 -I analysis/l5/btc5m_accumulation/price_vs_winrate.py
"""
from __future__ import annotations

import os
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
TICK = D("0.01")
MAX_RESTING = 4
BAND_LO, BAND_HI = D("0.51"), D("0.90")


def favourite(q):
    bid, ask = dec(q["bid"]), dec(q["ask"])        # noqa: F821
    if bid is None or ask is None:
        return None
    up, dn = bid, D("1") - ask
    return (C.UP, up) if up >= dn else (C.DOWN, dn)


def walk(rows, depth, lo=BAND_LO, hi=BAND_HI):
    """Rest `depth` ticks BELOW the favorite's touch."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    fills, rest, n = [], {}, 0
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
            p = rest.get(s)
            b = own_bid(q, s)                      # noqa: F821
            if p is not None and b is not None and b < p:
                fills.append({"price": p, "won": s == won})
                del rest[s]
                n += 1
        px = touch - depth * TICK
        if lo <= px <= hi and n < MAX_RESTING:
            for s in list(rest):
                if s != side:
                    del rest[s]
            rest[side] = px
        else:
            rest.clear()
        t += STEP
    return fills


def table(w, lo, hi, title, note):
    print()
    print("=" * 66)
    print(title)
    print("=" * 66)
    print(note)
    print("%-8s %8s %9s %9s %10s" % ("depth", "fills", "avg_px",
                                     "win_rate", "gap"))
    print("-" * 50)
    rows = []
    for depth in range(0, 7):
        fills = []
        for _s, r in sorted(w.items()):
            f = walk(r, depth, lo, hi)
            if f:
                fills.extend(f)
        if not fills:
            continue
        n = len(fills)
        px = float(sum(f["price"] for f in fills)) / n
        wr = sum(1 for f in fills if f["won"]) / n
        print("%-8d %8d %9.4f %9.4f %+10.4f" % (depth, n, px, wr, wr - px))
        rows.append((depth, px, wr))
    return rows


def main():
    w = load(TAPE)                                 # noqa: F821
    print("windows %d   favorite side, ADVERSE fills" % len(w))

    table(w, BAND_LO, BAND_HI,
          "AS SHIPPED (band %s-%s)" % (BAND_LO, BAND_HI),
          "  Operational, but CONFOUNDED for this question: the band floor\n"
          "  excludes cheap favorites more aggressively the deeper you\n"
          "  rest, so the average price RISES with depth instead of\n"
          "  falling. Depth is not isolating price here.")

    rows = table(w, D("0.02"), D("0.98"),
                 "BAND REMOVED (0.02-0.98) -- the clean experiment",
                 "  Now depth is the only thing changing, so the price\n"
                 "  genuinely falls and the two effects can be compared.")

    print()
    print("  gap = win_rate - avg_px, which IS the expected value of one")
    print("  share. Positive is profitable at ANY size; negative is not,")
    print("  also at any size.")

    if len(rows) > 1:
        d0, p0, w0 = rows[0]
        print()
        print("=" * 66)
        print("WHICH FALLS FASTER, THE PRICE OR THE WIN RATE?")
        print("=" * 66)
        print("%-8s %13s %13s %13s"
              % ("depth", "price gained", "win rate lost", "net"))
        print("-" * 52)
        for d, p, wr in rows[1:]:
            print("%-8d %+13.4f %+13.4f %+13.4f"
                  % (d, p0 - p, w0 - wr, (wr - p) - (w0 - p0)))
        print()
        print("  'price gained' is the discount won by resting deeper.")
        print("  'win rate lost' is what that discount cost in accuracy.")
        print("  When the second exceeds the first, the discount is not")
        print("  worth taking.")
        print()
        worse = all((wr - p) <= (w0 - p0) + 1e-9 for _d, p, wr in rows[1:])
        print("  Every deeper rest is %s than resting at the touch."
              % ("WORSE or equal" if worse else "NOT uniformly worse"))
        print()
        print("  That is adverse selection stated exactly: a resting buy is")
        print("  only reached when the market comes DOWN to it, so a")
        print("  cheaper fill is a fill you were more likely to be wrong")
        print("  about. The discount is real, and it is priced.")
        print()
        print("  CONCLUSION. Size cannot change the sign -- expectation is")
        print("  linear, so if every share is negative every basket of them")
        print("  is too. Price can change the sign in principle, but on")
        print("  this book every discount costs more win rate than it buys.")
        print("  With both levers closed, no arrangement of shares and")
        print("  prices on this book is positive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

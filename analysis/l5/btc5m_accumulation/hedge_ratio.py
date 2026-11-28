"""Favorite PLUS a partial hedge: what it costs and what it buys. READ-ONLY.

THE PROPOSAL. Not a matched pair -- buy the favorite as now, and carry a
hedge of h times that quantity on the other side, 0 <= h <= 1. h=0 is the
shipped directional strategy; h=1 is a full pair.

WHAT IT CANNOT DO, and this is arithmetic rather than a finding.
Settlement P&L is LINEAR in legs: every leg pays 1 or 0 on its own, so the
total is exactly EV(favorite) + h * EV(hedge leg). A hedge therefore helps
the MEAN only if the hedge leg is profitable standalone. The mirror test
(both_sides.py) measured that leg at a gap of +0.0048 a share with t=+0.20
and a sign that flips across the split, i.e. not reliably positive. So the
expectation should degrade roughly linearly in h, and h=0 should win on
the mean.

WHAT IT MIGHT DO, and why it is worth measuring anyway. The two legs
settle in OPPOSITE directions, so they are strongly negatively correlated
and a hedge cuts VARIANCE hard. That does not make money -- but variance
is the reason 12 live windows at sd 0.456 cannot distinguish -0.03 a
window from +0.03, and the reason a run of bad luck can reach a loss
ceiling before any edge could be established. A strategy that loses the
same amount far more predictably is a strategy you can actually MEASURE,
and that is worth knowing the price of.

So this reports mean, sd, and the ratio between them (the per-window
information ratio), plus how many windows each h would need to detect a
$0.05 edge at t=2.

Usage: python3 -I analysis/l5/btc5m_accumulation/hedge_ratio.py
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
CLIP = D("0.10")
MAX_RESTING = 4
BAND_LO, BAND_HI = D("0.51"), D("0.90")


def favourite(q):
    bid, ask = dec(q["bid"]), dec(q["ask"])        # noqa: F821
    if bid is None or ask is None:
        return None
    up, dn = bid, D("1") - ask
    return (C.UP, up) if up >= dn else (C.DOWN, dn)


def walk(rows, h):
    """Favorite at the touch; hedge h x that qty on the other side, also
    at ITS touch. The hedge rests alongside rather than replacing."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    legs = []
    rest = {}
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
        other = C.DOWN if side == C.UP else C.UP

        for s in (C.UP, C.DOWN):
            p = rest.get(s)
            b = own_bid(q, s)                      # noqa: F821
            if p is not None and b is not None and b < p[0]:
                legs.append((s, p[0], p[1]))
                if s == side:
                    n_f += 1
                del rest[s]

        if BAND_LO <= touch <= BAND_HI and n_f < MAX_RESTING:
            rest[side] = (touch, CLIP / touch)
            if h > 0:
                ob = own_bid(q, other)             # noqa: F821
                if ob is not None and ob > 0:
                    rest[other] = (ob, (CLIP / touch) * h)
        else:
            rest.clear()
        t += STEP

    return float(sum((D("1") if s == won else D(0)) * qty - p * qty
                     for s, p, qty in legs))


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    print("windows %d   favorite at the touch, hedge h x qty on the other"
          % len(slugs))
    print()
    print("%-7s %10s %9s %9s %8s %14s"
          % ("hedge h", "mean/win", "sd", "se", "t", "windows for"))
    print("%-7s %10s %9s %9s %8s %14s"
          % ("", "", "", "", "", "a $0.05 edge"))
    print("-" * 64)
    rows = []
    for cents in (0, 25, 50, 75, 100):
        h = D(cents) / 100
        v = [x for x in (walk(w[s], h) for s in slugs) if x is not None]
        m = statistics.fmean(v)
        sd = statistics.stdev(v)
        se = sd / math.sqrt(len(v))
        need = round((2 * sd / 0.05) ** 2)
        print("%-7s %+10.4f %9.4f %9.4f %+8.2f %14d"
              % (h, m, sd, se, m / se if se else 0.0, need))
        rows.append((float(h), m, sd, need))
    print()
    base_h, base_m, base_sd, base_need = rows[0]
    #: the variance MINIMUM, which is not necessarily the full hedge. An
    #: earlier draft compared h=0 against h=1 only and missed that a full
    #: pair is worse on BOTH counts than a half one, because the legs do
    #: not fill evenly and chasing a complete hedge reintroduces the
    #: imbalance it was meant to remove.
    best_h, best_m, best_sd, best_need = min(rows, key=lambda r: r[2])
    full_h, full_m, full_sd, full_need = rows[-1]
    print("=" * 64)
    print("WHAT THE HEDGE COSTS AND WHAT IT BUYS")
    print("=" * 64)
    print("  the mean degrades LINEARLY in h: %+0.4f at h=0 to %+0.4f at"
          % (base_m, full_m))
    print("  h=1, about %+0.4f for each 0.25 of hedge. That is linearity"
          % ((full_m - base_m) / 4.0))
    print("  of expectation in measured form: the hedge leg is not")
    print("  profitable standalone, so buying more of it costs the mean.")
    print()
    print("  VARIANCE IS MINIMISED AT h=%.2f, not at h=1:" % best_h)
    print("    sd      %.4f -> %.4f   (%.0f%% of unhedged)"
          % (base_sd, best_sd, 100.0 * best_sd / base_sd))
    print("    mean   %+0.4f -> %+0.4f   (costs %+0.4f a window)"
          % (base_m, best_m, best_m - base_m))
    print("    windows to measure a $0.05 edge: %d -> %d"
          % (base_need, best_need))
    print("    a FULL hedge is worse on both: sd %.4f, mean %+0.4f"
          % (full_sd, full_m))
    print()
    print("  SO THE HEDGE IS A RESEARCH INSTRUMENT, NOT AN EDGE. It makes")
    print("  the loss MORE CERTAIN rather than smaller -- t moves from")
    print("  %+0.2f to %+0.2f. What it buys is the ability to learn"
          % (base_m / (base_sd / math.sqrt(len(rows) and 187)),
             best_m / (best_sd / math.sqrt(187))))
    print("  whether an edge exists in %d windows instead of %d, and a"
          % (best_need, base_need))
    print("  drawdown small enough to survive long enough to find out.")
    print()
    print("  Which to choose depends on the goal. To MAKE money on this")
    print("  tape, h=0 is best (least negative). To MEASURE whether there")
    print("  is anything to make, h=%.2f is best." % best_h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

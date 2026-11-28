"""Entry price AND exit price, together. READ-ONLY.

WHAT THE LIVE DATA SCREAMS. Nearly every window has one side touching
0.0100. The loser does not stop at 8c -- it goes to a penny. So resting at
8c is catching a falling knife at a fixed price and losing 7 of the 8
cents, and `exit_on_recovery.py` only ever asked about selling the bounce
from an 8c ENTRY.

The untested combination is both at once. At a 2c entry a bounce to 4c
DOUBLES the stake and the most that can be lost is 2c -- an asymmetry
nothing like 8c -> 12c. Deep entry was measured before (flat edge per
dollar from 0.02 to 0.08) but ALWAYS held to settlement, which is exactly
the accounting that throws the bounce away.

ACCOUNTING, per window, one share a side:
  both sides fill      matched pair, pays 1.00       +(1 - 2*entry)
  one side fills, and
    it recovers to X   sold as a maker offer         +(X - entry)
    it does not        settles 1 or 0                +(1-entry) or -entry
A paired window is left alone; selling half of it re-opens direction.

MULTIPLE COMPARISONS ARE SEVERE HERE -- this is a 2D grid, so the best
cell is the best of many. Every cell reports out-of-sample separately and
the verdict applies the sqrt(2 ln N) bar over the whole grid.

Usage: python3 -I analysis/l5/btc5m_accumulation/entry_exit_grid.py
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


def shape(rows):
    """Per window: when each side first hit each entry, and its later high."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    path = []
    t = 0.0
    stop = WINDOW_S - CANCEL_LEAD_S                # noqa: F821
    while t < stop:
        got = quote_at(rows, t)                    # noqa: F821
        if got is not None:
            q, _age = got
            up = own_bid(q, C.UP)                  # noqa: F821
            dn = own_bid(q, C.DOWN)                # noqa: F821
            if up is not None and dn is not None:
                path.append((t, up, dn))
        t += STEP
    return (won, path) if len(path) > 10 else None


def walk(won, path, entry, exit_at):
    first = {}
    peak_after = {}
    for t, up, dn in path:
        for s, b in ((C.UP, up), (C.DOWN, dn)):
            if s not in first and b <= entry:
                first[s] = t
                peak_after[s] = b
            elif s in first and b > peak_after[s]:
                peak_after[s] = b
    if not first:
        return 0.0, "NONE"
    if len(first) == 2:
        return float(D(1) - 2*entry), "PAIR"
    s = next(iter(first))
    if exit_at is not None and peak_after[s] >= exit_at:
        return float(exit_at - entry), "EXIT"
    return float((D(1) if s == won else D(0)) - entry), "HELD"


def stats(v):
    m = statistics.fmean(v)
    se = statistics.stdev(v)/math.sqrt(len(v))
    return m, se, (m/se if se else 0.0)


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    shapes = []
    for s in slugs:
        sh = shape(w[s])
        if sh is not None:
            shapes.append(sh)
    cut = int(len(shapes)*0.67)
    print("windows %d   entry x exit grid, one share a side" % len(shapes))
    print("cells show NET c/w; (t) in sample; [OOS] held out")
    print()
    entries = [D(c)/100 for c in (2, 3, 4, 6, 8)]
    exits = [None] + [D(c)/100 for c in (4, 6, 8, 10, 12, 15, 20)]
    hdr = "%-7s" % "entry"
    for ex in exits:
        hdr += "%12s" % ("hold" if ex is None else str(ex))
    print(hdr)
    print("-" * len(hdr))
    best = None
    cells = 0
    for en in entries:
        line = "%-7s" % str(en)
        for ex in exits:
            if ex is not None and ex <= en:
                line += "%12s" % "-"
                continue
            rs = [walk(won, path, en, ex) for won, path in shapes]
            pnl = [r[0] for r in rs]
            m, se, t = stats(pnl)
            mo, _, _ = stats([r[0] for r in rs[cut:]])
            cells += 1
            if best is None or m > best[0]:
                best = (m, se, t, en, ex, mo, rs)
            line += "%12s" % ("%+.2f(%.1f)" % (100*m, t))
        print(line)
    print()
    m, se, t, en, ex, mo, rs = best
    n_ex = sum(1 for r in rs if r[1] == "EXIT")
    n_pr = sum(1 for r in rs if r[1] == "PAIR")
    n_hl = sum(1 for r in rs if r[1] == "HELD")
    print("BEST CELL  entry %s  exit %s" % (en, "hold" if ex is None else ex))
    print("  net %+.2f c/w   se %.2f   t %+.2f   OOS %+.2f"
          % (100*m, 100*se, t, 100*mo))
    print("  pair %4.1f%%   exit %4.1f%%   held %4.1f%%"
          % (100*n_pr/len(rs), 100*n_ex/len(rs), 100*n_hl/len(rs)))
    print("  worst case a window: -%.0fc a share (the entry, if it is held"
          % (100*float(en)))
    print("  to settlement and loses)")
    print()
    bar = math.sqrt(2*math.log(cells)) if cells > 1 else 2.0
    print("  searched %d cells -> best-of-noise expects |t| ~ %.2f" % (cells, bar))
    print("  reference: 0.08 held = the current strategy, +3.74 c/w, t +1.67")
    if t > bar and mo > 0:
        print("  VERDICT  clears the grid's own bar AND holds out of sample.")
    elif mo <= 0:
        print("  VERDICT  does not survive out of sample. Noise.")
    else:
        print("  VERDICT  does not clear the grid's bar. Treat as suggestive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

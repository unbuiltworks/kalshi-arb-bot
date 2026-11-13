"""Sell the recovery instead of holding to settlement. READ-ONLY.

THE GAP THIS ATTACKS. Every straddle measurement holds every leg to
settlement. 86.6% of windows end with ONE leg, bought at 8c, and it loses
that 8c about 97.5% of the time. Nothing has ever checked whether the side
that collapsed to 8c LATER RECOVERS inside the same window.

If it does, we can sell into the recovery. An OFFER fills when the price
RISES to it -- the mirror of a bid, and still maker-only -- so exiting is
mechanically available in a way that buying the winner is not.

WHY THIS IS NOT AN OPTIONS STRATEGY. There is no second instrument, no
spread structure and no premium. It is one position with an exit rule:
bought at 8c, sold on recovery to X, otherwise held to settlement. The
only question is whether the price path offers X.

THE ACCOUNTING, per leg:
  recovered to X    sold as maker       +(X - 0.08)
  never recovered   settles 1 or 0      +(1 - 0.08) or -0.08
A PAIRED window is left alone: a matched pair already pays 1.00 and
selling one side of it would re-open directional risk.

Usage: python3 -I analysis/l5/btc5m_accumulation/exit_on_recovery.py
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
ENTRY = D("0.08")


def walk(rows, exit_at):
    """Rest 8c both sides; on a lone leg, sell into a recovery to `exit_at`."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    filled = {}
    peak_after = {}
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
                if s not in filled and b <= ENTRY:
                    filled[s] = t
                    peak_after[s] = b
                elif s in filled and b > peak_after[s]:
                    #: an OFFER fills when the price RISES to it
                    peak_after[s] = b
        t += STEP

    if not filled:
        return {"case": "NONE", "pnl": 0.0, "exited": 0}
    if len(filled) == 2:
        #: a matched pair already pays 1.00; leave it alone
        return {"case": "PAIR", "pnl": float(D(1) - 2*ENTRY), "exited": 0}

    s = next(iter(filled))
    if exit_at is not None and peak_after[s] >= exit_at:
        return {"case": "EXIT", "pnl": float(exit_at - ENTRY), "exited": 1}
    return {"case": "HELD",
            "pnl": float((D(1) if s == won else D(0)) - ENTRY), "exited": 0}


def stats(v):
    m = statistics.fmean(v)
    se = statistics.stdev(v)/math.sqrt(len(v))
    return m, se, (m/se if se else 0.0)


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    cut = int(len(slugs)*0.67)
    print("windows %d   entry 8c both sides, one share a side" % len(slugs))
    print("a lone leg is SOLD as a maker if its price recovers to the exit")
    print()
    print("%-9s %7s %8s %9s %9s %7s %6s %9s %9s %5s" % (
        "exit at", "exit%", "held%", "pair%", "NET c/w", "se", "t",
        "in-samp", "OOS", "sign"))
    print("-" * 88)
    base = None
    for ex in (None, D("0.12"), D("0.15"), D("0.20"), D("0.25"),
               D("0.30"), D("0.40"), D("0.50")):
        rs = [r for r in (walk(w[s], ex) for s in slugs) if r]
        n = len(rs)
        pnl = [r["pnl"] for r in rs]
        m, se, t = stats(pnl)
        mi, _, _ = stats([r["pnl"] for r in rs[:cut]])
        mo, _, _ = stats([r["pnl"] for r in rs[cut:]])
        ex_n = sum(1 for r in rs if r["case"] == "EXIT")
        hl = sum(1 for r in rs if r["case"] == "HELD")
        pr = sum(1 for r in rs if r["case"] == "PAIR")
        if ex is None:
            base = m
        print("%-9s %6.1f%% %7.1f%% %8.1f%% %+9.2f %7.2f %+6.2f %+9.2f %+9.2f %5s"
              % ("hold" if ex is None else str(ex),
                 100*ex_n/n, 100*hl/n, 100*pr/n, 100*m, 100*se, t,
                 100*mi, 100*mo, "ok" if (mi > 0) == (mo > 0) else "NO"))
    print()
    print("'hold' is the current strategy: +3.74 c/w on this tape.")
    print("an exit rule only earns its place if it beats that, OOS too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

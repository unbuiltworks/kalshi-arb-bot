"""The LADDER, exactly as deployed. Not a sum of single-price straddles.

WHY THIS IS A DIFFERENT QUESTION. `straddle_decompose.py` prices ONE rung
at a time. The live ladder rests all six at once, and the legs are not
independent: when YES collapses to a nickel, EVERY rung on that side
fills. Observed live 2026-11-12 23:35z -- four UP rungs filled at an
average of 0.065 for 20 shares, DOWN never touched, and the window settled
-$1.30. The same move on a single 0.08 rung would have cost $0.40.

So the ladder multiplies the one-sided bleed. It also multiplies the pair
case, because a swing through both deep zones fills pairs at several
rungs. Which effect dominates is an empirical question and is the one that
decides whether the deployed configuration should keep running.

ACCOUNTING. Legs are matched rung by rung: a pair exists where both sides
filled at the SAME price, because that is what the venue leaves us holding
-- qty on each side, settled at 1.00 for the pair. Any excess on one side
is residual, settled 1 or 0 on the real outcome. Fees are zero on this
venue for these maker prints (observed: every per_order fee 0.0000).

Usage: python3 -I analysis/l5/btc5m_accumulation/ladder_decompose.py
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
CENTS = lambda *c: tuple(D(x) / 100 for x in c)    # noqa: E731

#: the deployed ladder first, then narrower and wider variants
LADDERS = {
    "0.05-0.10 (LIVE)": CENTS(5, 6, 7, 8, 9, 10),
    "0.08 only": CENTS(8),
    "0.05-0.08": CENTS(5, 6, 7, 8),
    "0.07-0.10": CENTS(7, 8, 9, 10),
    "0.05,0.08,0.10": CENTS(5, 8, 10),
    "0.10-0.15": CENTS(10, 11, 12, 13, 14, 15),
}


def walk(rows, rungs):
    """One share a side a rung. Returns per-window cents."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    filled = {C.UP: set(), C.DOWN: set()}
    t = 0.0
    while t < WINDOW_S - CANCEL_LEAD_S:            # noqa: F821
        got = quote_at(rows, t)                    # noqa: F821
        if got is not None:
            q, _age = got
            for s in (C.UP, C.DOWN):
                b = own_bid(q, s)                  # noqa: F821
                if b is None:
                    continue
                for r in rungs:
                    if r not in filled[s] and b <= r:
                        filled[s].add(r)
        t += STEP
    pair_pnl = res_pnl = D(0)
    pairs = residual = 0
    for r in rungs:
        up, dn = r in filled[C.UP], r in filled[C.DOWN]
        if up and dn:
            pair_pnl += D(1) - 2 * r              # locked, outcome-free
            pairs += 1
        elif up or dn:
            s = C.UP if up else C.DOWN
            res_pnl += (D(1) if s == won else D(0)) - r
            residual += 1
    return {"pair": float(pair_pnl), "res": float(res_pnl),
            "pnl": float(pair_pnl + res_pnl),
            "pairs": pairs, "residual": residual,
            "legs": len(filled[C.UP]) + len(filled[C.DOWN])}


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
    print("windows %d   ONE share a side a rung" % len(slugs))
    print("cents per window. PAIR is locked; RESID is the directional bleed")
    print()
    print("%-18s %6s %7s %8s %9s %9s %8s %7s %6s" % (
        "ladder", "legs/w", "pairs/w", "worst c", "PAIR c/w", "RESID c/w",
        "NET c/w", "se", "t"))
    print("-" * 88)
    out = {}
    for name, rungs in LADDERS.items():
        rs = [r for r in (walk(w[s], rungs) for s in slugs) if r is not None]
        if not rs:
            continue
        out[name] = rs
        n = len(rs)
        m, se, t = stats([r["pnl"] for r in rs])
        #: the worst case is every rung filled on one side, that side losing
        worst = -float(sum(rungs))
        print("%-18s %6.2f %7.2f %+8.1f %+9.2f %+9.2f %+8.2f %7.2f %+6.2f" % (
            name,
            statistics.fmean([r["legs"] for r in rs]),
            statistics.fmean([r["pairs"] for r in rs]),
            100 * worst,
            100 * statistics.fmean([r["pair"] for r in rs]),
            100 * statistics.fmean([r["res"] for r in rs]),
            100 * m, 100 * se, t))

    print()
    print("chronological out-of-sample, 67/33")
    print("%-18s %12s %12s %9s" % ("ladder", "in-sample", "OOS", "agree?"))
    print("-" * 56)
    for name, rs in out.items():
        mi, _, _ = stats([r["pnl"] for r in rs[:cut]])
        mo, _, _ = stats([r["pnl"] for r in rs[cut:]])
        print("%-18s %+11.2f %+11.2f %9s" % (
            name, 100 * mi, 100 * mo, "yes" if (mi > 0) == (mo > 0) else "NO"))

    print()
    live = out.get("0.05-0.10 (LIVE)")
    single = out.get("0.08 only")
    if live and single:
        ml, sel, tl = stats([r["pnl"] for r in live])
        ms, _, _ = stats([r["pnl"] for r in single])
        #: paired difference on the SAME windows -- the only fair comparison
        diff = [a["pnl"] - b["pnl"] for a, b in zip(live, single)]
        md, sed, td = stats(diff)
        print("LIVE ladder  %+.2f c/w   vs  single 0.08  %+.2f c/w"
              % (100 * ml, 100 * ms))
        print("paired difference %+.2f c/w   se %.2f   t %+.2f"
              % (100 * md, 100 * sed, td))
        print()
        if td > 2.0:
            print("VERDICT  the ladder beats the single rung on this tape.")
        elif td < -2.0:
            print("VERDICT  the ladder is WORSE than the single rung. The")
            print("         extra rungs buy more bleed than pairs.")
        else:
            print("VERDICT  no measurable difference between the ladder and")
            print("         the single rung (|t| < 2). The ladder multiplies")
            print("         the stake %.1fx without a measurable edge gain,"
                  % (sum(LADDERS["0.05-0.10 (LIVE)"])
                     / sum(LADDERS["0.08 only"])))
            print("         so it mostly buys variance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

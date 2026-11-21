"""Should the ladder stay out of the first minute? READ-ONLY.

POST HOC, AND SAID SO UP FRONT. `fill_timing.py` pre-registered that LATE
deep fills would be the bad ones and was falsified: the first 60 seconds
held 46 legs and ZERO winners, against an 8.2% breakeven, while the last
60 were essentially fair. That is a finding, not a prediction, and
"exclude the bucket that looked worst" is the single easiest way to fit
noise on this tape. So the in-sample figure here is worth nothing on its
own and the only column that counts is OOS.

THE MECHANISM IT WOULD NEED. An early collapse to a nickel is a violent
move carrying information -- it does not revert. A late drift to the same
price is convergence on a market that was always close, and the price is
about right. If that is real, delaying entry removes toxic legs.

WHY IT MIGHT STILL LOSE MONEY. A pair needs BOTH sides. The first leg of a
reversal pair is often exactly that early fill. Refusing it may destroy
more pairs than it saves bleed -- so this measures NET, with the pair and
residual contributions split out, not leg quality.

Usage: python3 -I analysis/l5/btc5m_accumulation/ladder_entry_delay.py
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
RUNGS = tuple(D(c) / 100 for c in (5, 6, 7, 8, 9, 10))
DELAYS = (0.0, 30.0, 60.0, 90.0, 120.0)


def walk(rows, delay, won):
    filled = {C.UP: set(), C.DOWN: set()}
    t = delay
    while t < WINDOW_S - CANCEL_LEAD_S:            # noqa: F821
        got = quote_at(rows, t)                    # noqa: F821
        if got is not None:
            q, _age = got
            for s in (C.UP, C.DOWN):
                b = own_bid(q, s)                  # noqa: F821
                if b is None:
                    continue
                for r in RUNGS:
                    if r not in filled[s] and b <= r:
                        filled[s].add(r)
        t += STEP
    pair = res = D(0)
    pairs = 0
    for r in RUNGS:
        up, dn = r in filled[C.UP], r in filled[C.DOWN]
        if up and dn:
            pair += D(1) - 2 * r
            pairs += 1
        elif up or dn:
            s = C.UP if up else C.DOWN
            res += (D(1) if s == won else D(0)) - r
    return {"pair": float(pair), "res": float(res),
            "pnl": float(pair + res), "pairs": pairs}


def stats(v):
    if len(v) < 2:
        return (statistics.fmean(v) if v else 0.0), 0.0, 0.0
    m = statistics.fmean(v)
    se = statistics.stdev(v) / math.sqrt(len(v))
    return m, se, (m / se if se else 0.0)


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = [s for s in sorted(w) if outcome(w[s]) is not None]  # noqa: F821
    cut = int(len(slugs) * 0.67)
    print("windows %d   ladder 0.05-0.10, one share a side a rung" % len(slugs))
    print("POST HOC: the 0-60s bucket was chosen BY this tape. Read OOS only.")
    print()
    print("%-9s %8s %9s %9s %9s %7s %6s %11s %10s" % (
        "entry", "pairs/w", "PAIR c/w", "RESID c/w", "NET c/w", "se", "t",
        "in-sample", "OOS"))
    print("-" * 86)
    base_oos = None
    for d in DELAYS:
        rs = [walk(w[s], d, outcome(w[s])) for s in slugs]   # noqa: F821
        m, se, t = stats([r["pnl"] for r in rs])
        mi, _, _ = stats([r["pnl"] for r in rs[:cut]])
        mo, seo, _ = stats([r["pnl"] for r in rs[cut:]])
        if d == 0.0:
            base_oos = [r["pnl"] for r in rs[cut:]]
        print("%-9s %8.2f %+9.2f %+9.2f %+9.2f %7.2f %+6.2f %+10.2f %+9.2f"
              % ("T+%ds" % int(d),
                 statistics.fmean([r["pairs"] for r in rs]),
                 100 * statistics.fmean([r["pair"] for r in rs]),
                 100 * statistics.fmean([r["res"] for r in rs]),
                 100 * m, 100 * se, t, 100 * mi, 100 * mo))

    print()
    #: the honest test: paired OOS difference against resting from T+0
    print("paired OOS difference vs resting from T+0 (same windows)")
    print("%-9s %12s %8s %7s" % ("entry", "diff c/w", "se", "t"))
    print("-" * 40)
    for d in DELAYS[1:]:
        rs = [walk(w[s], d, outcome(w[s])) for s in slugs]    # noqa: F821
        diff = [x["pnl"] - y for x, y in zip(rs[cut:], base_oos)]
        md, sed, td = stats(diff)
        print("%-9s %+11.2f %8.2f %+6.2f"
              % ("T+%ds" % int(d), 100 * md, 100 * sed, td))

    print()
    print("VERDICT  a delay is worth taking only if its paired OOS")
    print("         difference is positive with |t| >= 2. Anything less is")
    print("         the 0-60s bucket being re-read on the data that")
    print("         produced it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

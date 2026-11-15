"""Is the pair rate PREDICTABLE from the previous window? READ-ONLY.

WHY THIS QUESTION NOW. The tape gave a 10.2% pair rate over 189 windows.
The live shadow gave 2.8% over 108. Same code -- shadow_crosscheck.py
proves the logic reproduces the tape to the decimal place -- so the pair
rate is not a property of the strategy, it is a property of the MARKET
THAT DAY. A pair needs a REVERSAL, and some stretches reverse while others
trend.

If that regime persists even weakly, we do not need a better strategy. We
need to sit out the trending stretches. An unconditional edge of t +0.71
on 297 windows could be worth having inside the right regime and worth
nothing outside it.

NO LOOKAHEAD, and that is the whole discipline here. Every predictor is
computed from the PREVIOUS window only and used to call the NEXT one.
Anything else measures hindsight.

PREDICTORS, all from window i-1:
  chop      range of own UP price divided by |net move| -- high means it
            wandered and came back, low means it went one way
  flips     direction changes in the UP price, as a count
  prior     whether window i-1 itself paired
  depth     how deep the losing side got (a proxy for trend strength)

Usage: python3 -I analysis/l5/btc5m_accumulation/regime_filter.py
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


def features(rows):
    """Everything about ONE window: its outcome, and its own shape."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    path = []
    filled = set()
    t = 0.0
    stop = WINDOW_S - CANCEL_LEAD_S                # noqa: F821
    while t < stop:
        got = quote_at(rows, t)                    # noqa: F821
        if got is not None:
            q, _age = got
            up = own_bid(q, C.UP)                  # noqa: F821
            if up is not None:
                path.append(float(up))
            for s in (C.UP, C.DOWN):
                b = own_bid(q, s)                  # noqa: F821
                if b is not None and b <= ENTRY:
                    filled.add(s)
        t += STEP
    if len(path) < 10:
        return None
    rng = max(path) - min(path)
    net = abs(path[-1] - path[0])
    flips = 0
    last = None
    for a, b in zip(path, path[1:]):
        if b == a:
            continue
        d = 1 if b > a else -1
        if last is not None and d != last:
            flips += 1
        last = d
    case = {2: "BOTH", 1: "ONE", 0: "NONE"}[len(filled)]
    pnl = 0.0
    for s in filled:
        pnl += (1.0 if s == won else 0.0) - float(ENTRY)
    if len(filled) == 2:
        pnl = 1.0 - 2*float(ENTRY)
    return {"case": case, "pnl": pnl,
            "chop": (rng / net) if net > 1e-9 else 999.0,
            "flips": flips, "depth": min(path),
            "paired": len(filled) == 2}


def stats(v):
    if len(v) < 2:
        return 0.0, 0.0, 0.0
    m = statistics.fmean(v)
    se = statistics.stdev(v)/math.sqrt(len(v))
    return m, se, (m/se if se else 0.0)


def report(name, pairs_lo, pairs_hi, cut):
    """pairs_* are (n, pair_count, [pnl]) for below/above the split."""
    for lbl, (n, k, pnl) in (("BELOW", pairs_lo), ("ABOVE", pairs_hi)):
        if n < 10:
            print("   %-5s %-10s too few" % (lbl, name))
            continue
        m, se, t = stats(pnl)
        print("   %-5s n=%-4d pair %5.1f%%   net %+6.2f c/w  se %5.2f  t %+5.2f"
              % (lbl, n, 100*k/n, 100*m, 100*se, t))


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    seq = []
    for s in slugs:
        f = features(w[s])
        if f is not None:
            seq.append(f)
    print("windows with features: %d" % len(seq))
    base_pairs = sum(1 for f in seq if f["paired"])
    m, se, t = stats([f["pnl"] for f in seq])
    print("UNCONDITIONAL   pair %.1f%%   net %+.2f c/w   t %+.2f"
          % (100*base_pairs/len(seq), 100*m, t))
    print()
    print("CONDITIONED ON THE PREVIOUS WINDOW (no lookahead):")
    print()
    for key, label in (("chop", "chop ratio"), ("flips", "flips"),
                       ("depth", "prior depth")):
        vals = [f[key] for f in seq[:-1]]
        cut = statistics.median(vals)
        lo = [(0, 0, [])]
        n_lo = k_lo = 0; p_lo = []
        n_hi = k_hi = 0; p_hi = []
        for prev, nxt in zip(seq, seq[1:]):
            if prev[key] <= cut:
                n_lo += 1; k_lo += nxt["paired"]; p_lo.append(nxt["pnl"])
            else:
                n_hi += 1; k_hi += nxt["paired"]; p_hi.append(nxt["pnl"])
        print("  %s  (median split at %.3f)" % (label, cut))
        report(label, (n_lo, k_lo, p_lo), (n_hi, k_hi, p_hi), cut)
        print()
    #: does a pair predict a pair?
    n_a = k_a = 0; p_a = []
    n_b = k_b = 0; p_b = []
    for prev, nxt in zip(seq, seq[1:]):
        if prev["paired"]:
            n_a += 1; k_a += nxt["paired"]; p_a.append(nxt["pnl"])
        else:
            n_b += 1; k_b += nxt["paired"]; p_b.append(nxt["pnl"])
    print("  previous window PAIRED?")
    report("paired", (n_b, k_b, p_b), (n_a, k_a, p_a), 0)
    print("   (BELOW = prior did not pair, ABOVE = prior paired)")
    print()
    print("a regime filter is only worth having if one side of a split")
    print("beats the unconditional rate by more than noise.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

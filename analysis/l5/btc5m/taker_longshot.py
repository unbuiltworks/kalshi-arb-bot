"""Buying the cheaper side as a TAKER, mid-window. No fill model at all.

WHY A TAKER STRATEGY, AFTER MONTHS OF MAKER-FIRST. Every maker variant tested
on this tape loses, and they all lose to the same thing. A passive buy is
filled precisely when the price moves against it, and that cost is MEASURED,
repeatedly, at about 6-7 cents a share:

  - `unmatched_exit_value.py`, 226 settled BTC5m windows: cutting an
    unmatched leg at the prevailing bid the instant we hold it costs -0.060
    to -0.072 a leg. That gap between what we paid and what it is instantly
    worth IS the adverse selection.
  - `l5_btc5m_controller`'s header puts it at ~6 cents from an independent
    proxy, and `adverse_selection_per_share` has carried 0.06 all along.

The taker fee on this venue is 0.095 * p * (1-p) per share, which at a price
of 0.25 is 1.8 cents and at 0.15 is 1.2 cents. SO CROSSING THE SPREAD IS
THREE TO SIX TIMES CHEAPER THAN RESTING IN IT. That is the whole thesis, and
it inverts the project's maker-first assumption.

A taker entry also removes every modelling assumption that made the maker
results arguable: no queue position, no partial fills, no "would it have
filled", no cancellation risk. We pay the displayed offer and we are filled.

WHAT IS MEASURED. At `t` seconds into the window, buy the cheaper side at its
own offer when that offer is at or below `maxpx`, pay the taker fee, hold to
settlement. Reported with a CHRONOLOGICAL out-of-sample split, because the
configuration space (t x maxpx) was searched and a single lucky cell proves
nothing -- only a cell that holds its sign and size in both halves is worth
anything.

HONEST LIMITS. The offer is assumed available for our whole size at the
displayed price; at a $5 cap and a ~0.25 price that is ~20 shares against
quoted sizes in the hundreds, so it is a mild assumption, but it is one.
Settlement is taken from the last quote of the window (bid >= 0.9 => UP won,
<= 0.1 => DOWN won), which resolved 226 of 230 windows -- the 4 undecided are
discarded and counted, never guessed. Nothing here is a fill and none of it
is realized P&L.
"""
from __future__ import annotations

import collections
import json
import random
import statistics as st
import sys

TAKER_COEF = 0.095
DECISIVE_HI, DECISIVE_LO = 0.9, 0.1
random.seed(20261119)


def taker_fee(price: float) -> float:
    p = min(max(price, 0.0), 1.0)
    return TAKER_COEF * p * (1.0 - p)


def settled_windows(path):
    by = collections.defaultdict(list)
    for r in json.load(open(path)):
        by[r["m"]].append(r)
    out, undecided = [], 0
    for m, v in sorted(by.items()):
        v.sort(key=lambda r: r["t"])
        if len(v) < 8:
            continue
        last = v[-1]["bid"]
        if last >= DECISIVE_HI:
            out.append((m, v, 1))
        elif last <= DECISIVE_LO:
            out.append((m, v, 0))
        else:
            undecided += 1
    return out, undecided


def snapshot_at(v, t):
    best = None
    for r in v:
        if r["t"] <= t:
            best = r
        else:
            break
    return best


def trades(sample, t, maxpx):
    """Net P&L per share for every window that offered a cheap enough side."""
    out = []
    for _m, v, res in sample:
        r = snapshot_at(v, t)
        if r is None:
            continue
        up_ask, dn_ask = r["ask"], 1.0 - r["bid"]
        if up_ask <= dn_ask:
            px, won = up_ask, res == 1
        else:
            px, won = dn_ask, res == 0
        if not (0.0 < px <= maxpx):
            continue
        out.append((1.0 if won else 0.0) - px - taker_fee(px))
    return out


def stats(p):
    n = len(p)
    mean = sum(p) / n
    se = st.pstdev(p) / (n ** 0.5)
    boot = sorted(sum(random.choices(p, k=n)) / n for _ in range(10000))
    return n, mean, se, boot[250], boot[9750]


def run(path):
    wins, undecided = settled_windows(path)
    half = len(wins) // 2
    print("=" * 78)
    print("BTC5m TAKER LONGSHOT -- %d settled windows (%d undecided, discarded)"
          % (len(wins), undecided))
    print("buy the cheaper side at its OFFER, pay the taker fee, hold to"
          " settlement")
    print("=" * 78)
    print("%5s %6s %6s %9s %9s %-20s %9s %9s"
          % ("t", "maxpx", "n", "mean/sh", "se", "95% CI", "1st half", "2nd half"))
    for t in (60.0, 120.0, 180.0, 240.0):
        for maxpx in (0.20, 0.30, 0.40):
            allp = trades(wins, t, maxpx)
            if len(allp) < 30:
                continue
            n, mean, se, lo, hi = stats(allp)
            a = trades(wins[:half], t, maxpx)
            b = trades(wins[half:], t, maxpx)
            fa = (sum(a) / len(a)) if len(a) >= 15 else None
            fb = (sum(b) / len(b)) if len(b) >= 15 else None
            stable = (fa is not None and fb is not None
                      and fa > 0 and fb > 0)
            print("%5.0f %6.2f %6d %+9.4f %9.4f [%+0.4f, %+0.4f] %9s %9s %s"
                  % (t, maxpx, n, mean, se, lo, hi,
                     ("%+0.4f" % fa) if fa is not None else "-",
                     ("%+0.4f" % fb) if fb is not None else "-",
                     "<== both halves positive" if stable else ""))
        print()
    print("=" * 78)
    print("A cell is only interesting if BOTH halves are positive: the grid")
    print("was searched, so one good cell in isolation is a selection effect.")
    print("Hypothetical executions against a recorded quote tape."
          " NOT fills, NOT realized P&L.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1]))

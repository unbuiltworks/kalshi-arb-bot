"""Do ENTRY GATES rescue favorite accumulation? Measured. READ-ONLY.

THE PROBLEM THIS ATTACKS. Accumulation loses about 4.7 cents a share
against the book mid at the moment of the fill (ECONOMICS.md). Resting a
maker buy at the bid should EARN half the spread, so a loss that large is
not a mispriced-probability problem -- it is ADVERSE SELECTION. Our bid
gets hit precisely when the bid is about to fall. Calibrated probability
features (strike distance, volatility, time-to-expiry, momentum) were
already tested and all worsened out-of-sample log loss; this file tests a
different hypothesis class, which is WHEN TO REST AT ALL rather than what
the fair price is.

WHY A GATE COULD WORK IN PRINCIPLE, and the honest counter: a wider spread
pays more cushion per fill, and a book that has not moved recently is less
likely to be carrying information. If adverse selection is concentrated in
identifiable states, excluding them lifts the average. If instead it is the
PRICE OF THE FILL ITSELF -- the fill only ever arrives because the move
arrived -- no state filter can help, because the toxicity is not a
property of the state we see before the fill.

GATES TESTED, each suppressing new placements only (cancels and fills are
untouched, so a gate can never invent an exit):

  SPREAD      rest only when ask-bid is at least k ticks
  QUIET       rest only when the mid has moved no more than k cents
              over the preceding 20 s
  EARLY/LATE  rest only in the first / last part of the window
  CEILING     rest only while the favorite is at or below a price

METHOD. Chronological split, never shuffled. Window-clustered means: each
window contributes one observation, because the rows inside a window share
one settlement. Every arm is reported against NO-TRADE, which nets exactly
zero and is the benchmark that actually matters. A gate that merely loses
less than ungated accumulation has not found an edge.

Usage: python3 -I analysis/l5/btc5m_accumulation/replay_gates.py
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

TICK = D("0.01")


def mid_at(rows, t):
    got = quote_at(rows, t)                        # noqa: F821
    if got is None:
        return None
    q, _age = got
    b, a = dec(q["bid"]), dec(q["ask"])            # noqa: F821
    return None if b is None or a is None else (b + a) / 2


# ---- the gates ------------------------------------------------------
def g_spread(k):
    def gate(q, t, age, rows):
        b, a = dec(q["bid"]), dec(q["ask"])        # noqa: F821
        return b is not None and a is not None and (a - b) >= k * TICK
    return gate


def g_quiet(cents, look_s=20.0):
    def gate(q, t, age, rows):
        now, then = mid_at(rows, t), mid_at(rows, max(0.0, t - look_s))
        if now is None or then is None:
            return False
        return abs(now - then) <= cents * TICK
    return gate


def g_early(upto):
    return lambda q, t, age, rows: t <= upto


def g_late(after):
    return lambda q, t, age, rows: t >= after


def g_ceiling(cap):
    def gate(q, t, age, rows):
        b, a = dec(q["bid"]), dec(q["ask"])        # noqa: F821
        if b is None or a is None:
            return False
        fav = max(b, D("1") - a)                   # the favorite's own bid
        return fav <= cap
    return gate


def g_and(*gs):
    return lambda q, t, age, rows: all(g(q, t, age, rows) for g in gs)


ARMS = [
    ("ungated (shipped)", None),
    ("SPREAD >= 2 ticks", g_spread(2)),
    ("SPREAD >= 3 ticks", g_spread(3)),
    ("SPREAD >= 4 ticks", g_spread(4)),
    ("QUIET <= 1c / 20s", g_quiet(1)),
    ("QUIET <= 2c / 20s", g_quiet(2)),
    ("QUIET <= 3c / 20s", g_quiet(3)),
    ("EARLY t <= 60s", g_early(60)),
    ("EARLY t <= 120s", g_early(120)),
    ("EARLY t <= 180s", g_early(180)),
    ("LATE  t >= 180s", g_late(180)),
    ("LATE  t >= 240s", g_late(240)),
    ("CEILING fav <= 0.60", g_ceiling(D("0.60"))),
    ("CEILING fav <= 0.70", g_ceiling(D("0.70"))),
    ("CEILING fav <= 0.80", g_ceiling(D("0.80"))),
    ("SPREAD>=3 AND QUIET<=2c", g_and(g_spread(3), g_quiet(2))),
    ("SPREAD>=3 AND EARLY<=120", g_and(g_spread(3), g_early(120))),
]


def net_of(r):
    return float(sum((f["qty"] if f["won"] else D(0)) - f["qty"] * f["price"]
                     for f in r["fills"]))


def shares_of(r):
    return float(sum(f["qty"] for f in r["fills"]))


def stats(v):
    if len(v) < 2:
        return (statistics.fmean(v) if v else 0.0), 0.0, 0.0
    m = statistics.fmean(v)
    se = statistics.stdev(v) / math.sqrt(len(v))
    return m, se, (m / se if se else 0.0)


def run_arm(windows, gate):
    out = {}
    for slug, rows in sorted(windows.items()):
        r = run_window(rows, tier="ADVERSE", gate=gate)   # noqa: F821
        if r:
            out[slug] = r
    return out


def line(name, runs):
    nets = [net_of(r) for r in runs.values()]
    m, se, t = stats(nets)
    sh = sum(shares_of(r) for r in runs.values())
    fills = sum(len(r["fills"]) for r in runs.values())
    traded = sum(1 for r in runs.values() if r["fills"])
    per_share = (sum(net_of(r) for r in runs.values()) / sh) if sh else 0.0
    print("%-26s %5d %6d %7d %8.2f %+9.4f %7.4f %+6.2f %+9.4f"
          % (name, len(runs), traded, fills, sh, m, se, t, per_share))


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    cut = int(len(slugs) * 0.67)
    print("windows on tape %d   chronological OOS cut at %s"
          % (len(slugs), slugs[cut][-16:]))
    print()
    hdr = ("%-26s %5s %6s %7s %8s %9s %7s %6s %9s"
           % ("arm", "wins", "traded", "fills", "shares", "net/win",
              "se", "t", "net/share"))
    results = {}
    for block_name, blk in (("ALL", slugs), ("OOS (last third)", slugs[cut:])):
        print("=" * 100)
        print("BLOCK: %s   (%d windows)" % (block_name, len(blk)))
        print("=" * 100)
        print(hdr)
        print("-" * 100)
        print("%-26s %5d %6d %7d %8.2f %+9.4f %7.4f %+6.2f %+9.4f"
              % ("NO-TRADE", len(blk), 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0))
        for name, gate in ARMS:
            key = (block_name, name)
            runs = results.setdefault(
                name, run_arm(w, gate)) if block_name == "ALL" \
                else results[name]
            sub = {s: runs[s] for s in blk if s in runs}
            line(name, sub)
            del key
        print()

    # ---- the verdict, stated plainly
    print("=" * 100)
    print("BEST ARM vs NO-TRADE, out of sample")
    print("=" * 100)
    oos = slugs[cut:]
    best, bm = None, None
    for name, _g in ARMS:
        sub = {s: results[name][s] for s in oos if s in results[name]}
        nets = [net_of(r) for r in sub.values()]
        if not any(r["fills"] for r in sub.values()):
            continue
        m, se, t = stats(nets)
        if bm is None or m > bm:
            best, bm, bse, bt = name, m, se, t
    if best is None:
        print("no arm placed a single fill out of sample.")
        return 0
    print("best: %s" % best)
    print("   net/win %+0.4f   se %.4f   t %+0.2f" % (bm, bse, bt))
    print()
    if bm <= 0:
        print("VERDICT: NEGATIVE. No gate makes favorite accumulation")
        print("profitable out of sample. No-trade still dominates every arm.")
    elif bt < 2.0:
        print("VERDICT: NOT CREDIBLE. The best arm is positive but t=%+0.2f"
              % bt)
        print("is inside noise at this sample size; promoting it would be")
        print("fitting the tape, not finding an edge.")
    else:
        print("VERDICT: a positive, statistically credible arm exists.")
        print("It still needs an independent out-of-sample tape before any")
        print("live change -- this block was used to CHOOSE it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Does hedging the flip help? Measured, on the aligned tape. READ-ONLY.

THE QUESTION the owner asked: "mathematically figure out how to hedge when
it flips."

THE ARITHMETIC, first, because it decides what is worth measuring. Hold q
shares of the old favorite at average cost c; the favorite flips; buy q of
the new favorite at d. A matched pair pays $1 whichever way it settles, so

    hedged   net per share = 1 - (c + d)      DETERMINISTIC
    held     net per share = p_held - c       expected, still random

    hedging beats holding  iff  1 - (c+d) > p_held - c  iff  d < 1 - p_held

Resting as a MAKER on the new favorite pays its bid, d = 1 - old_fav_ask,
so the test becomes p_held < old_fav_ask: hedge when the side you hold is
worth less than someone will still pay for it.

THE STRUCTURAL PROBLEM. This strategy only ever buys a favorite at $0.51 or
more. When that side stops being the favorite, the OTHER side is the
favorite, so it too costs about $0.51 or more. The pair therefore costs
about $1.02 or more and locks a GUARANTEED LOSS. `hard_pair_ceiling` (1.00)
refuses exactly that. So the interesting question is not whether the hedge
is profitable -- it cannot be -- but whether it LOSES LESS than holding,
and how often the ceiling lets it happen at all.

THREE ARMS, chronologically split, never shuffled, against no-trade:

  NO-TRADE   place nothing. Net exactly zero. The benchmark that matters.
  HOLD       accumulation as shipped: hedge_enabled False, carry the
             abandoned side to settlement.
  HEDGE      the same accumulation with the controller's OWN hedge turned
             on (hedge_enabled True, reserve_mode COMPLETION), which rests
             a maker buy on the new favorite for the unmatched quantity and
             refuses any pair above the ceiling.

NO CONTROLLER CHANGE IS NEEDED for the HEDGE arm: the logic already exists
and is reached by parameters alone. Nothing here is wired to live.

Usage: python3 -I analysis/l5/btc5m_accumulation/replay_hedge.py
"""
from __future__ import annotations

import collections
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

CAP = D("0.45")
BASE = dict(entry_mode=C.ACCUMULATE, max_exposure_usd=CAP,
            clip_usd=C.Params().clip_usd,
            max_resting_entries=C.Params().max_resting_entries,
            accumulate_depth_ticks=0)
ARMS = {
    "HOLD  (shipped)": C.Params(**dict(BASE, hedge_enabled=False,
                                       reserve_mode="NONE")),
    "HEDGE (maker, ceiling 1.00)": C.Params(**dict(BASE, hedge_enabled=True,
                                                   reserve_mode="COMPLETION")),
}


def run_arm(windows, params):
    global PARAMS
    PARAMS = params
    globals()["PARAMS"] = params
    out = {}
    for slug, rows in sorted(windows.items()):
        r = run_window(rows, tier="ADVERSE")       # noqa: F821
        if r:
            out[slug] = r
    return out


def net_of(r):
    return float(sum((f["qty"] if f["won"] else D(0)) - f["qty"] * f["price"]
                     for f in r["fills"]))


def legs(r):
    up = sum((f["qty"] for f in r["fills"] if f["side"] == C.UP), D(0))
    dn = sum((f["qty"] for f in r["fills"] if f["side"] == C.DOWN), D(0))
    return up, dn


def stats(v):
    if len(v) < 2:
        return (statistics.fmean(v) if v else 0.0), 0.0, 0.0
    m = statistics.fmean(v)
    se = statistics.stdev(v) / math.sqrt(len(v))
    return m, se, (m / se if se else 0.0)


def report(name, runs):
    nets = [net_of(r) for r in runs.values()]
    m, se, t = stats(nets)
    fills = sum(len(r["fills"]) for r in runs.values())
    matched = 0
    for r in runs.values():
        up, dn = legs(r)
        matched += float(min(up, dn))
    total = sum(float(sum(f["qty"] for f in r["fills"]))
                for r in runs.values())
    print("%-28s %6d %7d %9.3f %9.4f %+10.4f %8.4f %+6.2f"
          % (name, len(runs), fills, matched,
             (matched / total if total else 0.0), m, se, t))


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    cut = int(len(slugs) * 0.67)
    blocks = (("ALL", slugs), ("early", slugs[:cut]),
              ("OOS", slugs[cut:]))
    print("windows on tape %d   chronological cut at %s"
          % (len(slugs), slugs[cut][-16:]))
    print()
    print("%-28s %6s %7s %9s %9s %10s %8s %6s"
          % ("arm / block", "wins", "fills", "matched", "matched%",
             "net/win", "se", "t"))
    print("-" * 94)
    runs = {name: run_arm(w, p) for name, p in ARMS.items()}
    for name, rr in runs.items():
        for bname, blk in blocks:
            sub = {s: rr[s] for s in blk if s in rr}
            report("%s / %s" % (name.split()[0], bname), sub)
        print("-" * 94)
    n = len(runs["HOLD  (shipped)"])
    print("%-28s %6d %7d %9.3f %9.4f %+10.4f %8.4f %+6.2f"
          % ("NO-TRADE / ALL", n, 0, 0.0, 0.0, 0.0, 0.0, 0.0))
    print()

    # ---- the flip itself: what the ceiling actually allowed
    print("=" * 94)
    print("WHAT HAPPENED AT THE FLIPS")
    print("=" * 94)
    hold, hedge = runs["HOLD  (shipped)"], runs["HEDGE (maker, ceiling 1.00)"]
    both = sorted(set(hold) & set(hedge))
    flipped = [s for s in both if min(legs(hedge[s])) > 0]
    print("windows where the HEDGE arm actually formed a pair: %d of %d"
          % (len(flipped), len(both)))
    pairs = []
    for s in flipped:
        up_f = [f for f in hedge[s]["fills"] if f["side"] == C.UP]
        dn_f = [f for f in hedge[s]["fills"] if f["side"] == C.DOWN]
        if not up_f or not dn_f:
            continue
        cu = sum(f["qty"] * f["price"] for f in up_f) / sum(f["qty"]
                                                            for f in up_f)
        cd = sum(f["qty"] * f["price"] for f in dn_f) / sum(f["qty"]
                                                            for f in dn_f)
        pairs.append(float(cu + cd))
    if pairs:
        print("pair cost on those windows: mean %.4f  min %.4f  max %.4f"
              % (statistics.fmean(pairs), min(pairs), max(pairs)))
        print("pairs costing MORE than $1.00 (a locked loss): %d of %d"
              % (sum(1 for p in pairs if p > 1.0), len(pairs)))
    print()
    d = [net_of(hedge[s]) - net_of(hold[s]) for s in both]
    m, se, t = stats(d)
    print("HEDGE minus HOLD, same windows, paired:")
    print("   mean %+0.4f a window   se %.4f   t %+0.2f   n %d"
          % (m, se, t, len(d)))
    better = sum(1 for x in d if x > 1e-9)
    worse = sum(1 for x in d if x < -1e-9)
    print("   hedging was better in %d windows, worse in %d, equal in %d"
          % (better, worse, len(d) - better - worse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

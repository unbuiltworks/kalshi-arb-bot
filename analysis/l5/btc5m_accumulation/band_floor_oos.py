"""Is the loss really concentrated in CHEAP favorites? Properly tested.

THE APPEARANCE. `replay.py`'s per-price table is striking: the three
cheapest buckets carry -$4.53 of the -$5.28 total loss, and at 0.50-0.54
the "favorite" wins only 46.8% of the time -- below a coin flip. It reads
like a mechanism: near 0.51 the market is a near tie, the favorite label
is within noise, and a maker fill means the lead was already going.

WHY THAT TABLE CANNOT SETTLE IT, and this is the trap:

  1. It conditions on the price a fill HAPPENED at, which is chosen after
     the fact. Picking the buckets that look good is selection on noise.
  2. Most of the VOLUME sits in the cheap buckets (63.3 of 108.6 shares),
     so they carry most of the loss even if the per-share rate were equal.
  3. Per-bucket samples are small. The two POSITIVE buckets are
     +0.0101/share on 73 fills (t ~ +0.2) and +0.0316 on 53 (t ~ +0.6):
     both indistinguishable from zero.
  4. Most importantly, raising the band floor is NOT the same experiment.
     Skipping a 0.55 favorite does not hand you its 0.70 fill later --
     the trajectory changes, so the fills at a given price under a higher
     floor are different fills.

THE EXECUTABLE QUESTION, which is the only one that pays: if the band
floor is raised, does the realized result improve OUT OF SAMPLE? That is
what this measures -- chronological split, never shuffled,
window-clustered standard errors because rows in a window share one
settlement, and every arm against no-trade.

Usage: python3 -I analysis/l5/btc5m_accumulation/band_floor_oos.py
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

FLOORS = ("0.51", "0.55", "0.60", "0.65", "0.70", "0.75", "0.80")


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


def main():
    global PARAMS
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    cut = int(len(slugs) * 0.67)
    keep = PARAMS                                  # noqa: F821
    runs = {}
    for lo in FLOORS:
        PARAMS = C.Params(**dict(vars(keep), band_lo=D(lo)))
        globals()["PARAMS"] = PARAMS
        out = {}
        for slug, rows in sorted(w.items()):
            r = run_window(rows, tier="ADVERSE")   # noqa: F821
            if r:
                out[slug] = r
        runs[lo] = out
    PARAMS = keep
    globals()["PARAMS"] = keep

    print("windows %d   chronological OOS cut at %s"
          % (len(slugs), slugs[cut][-16:]))
    for name, blk in (("IN-SAMPLE (first 2/3)", slugs[:cut]),
                      ("OUT-OF-SAMPLE (last 1/3)", slugs[cut:])):
        print()
        print("=" * 88)
        print("%s   %d windows" % (name, len(blk)))
        print("=" * 88)
        print("%-9s %7s %8s %10s %9s %8s %6s %10s"
              % ("band_lo", "fills", "shares", "net/win", "se", "t",
                 "", "net/share"))
        print("-" * 88)
        print("%-9s %7d %8.2f %+10.4f %9.4f %8.2f %6s %+10.4f"
              % ("NO-TRADE", 0, 0.0, 0.0, 0.0, 0.0, "", 0.0))
        for lo in FLOORS:
            sub = {s: runs[lo][s] for s in blk if s in runs[lo]}
            nets = [net_of(r) for r in sub.values()]
            m, se, t = stats(nets)
            sh = sum(shares_of(r) for r in sub.values())
            f = sum(len(r["fills"]) for r in sub.values())
            ps = (sum(net_of(r) for r in sub.values()) / sh) if sh else 0.0
            print("%-9s %7d %8.2f %+10.4f %9.4f %8.2f %6s %+10.4f"
                  % (lo, f, sh, m, se, t, "", ps))

    # ---- does the IN-SAMPLE best hold up OUT OF SAMPLE?
    print()
    print("=" * 88)
    print("DOES THE IN-SAMPLE PICK SURVIVE?")
    print("=" * 88)

    def mean_on(lo, blk):
        sub = {s: runs[lo][s] for s in blk if s in runs[lo]}
        return stats([net_of(r) for r in sub.values()])

    best_in = max(FLOORS, key=lambda lo: mean_on(lo, slugs[:cut])[0])
    mi = mean_on(best_in, slugs[:cut])
    mo = mean_on(best_in, slugs[cut:])
    full = mean_on(best_in, slugs)
    print("  best floor in sample      : %s  (%+0.4f/window, t %+0.2f)"
          % (best_in, mi[0], mi[2]))
    print("  that same floor OOS       : %+0.4f/window  se %.4f  t %+0.2f"
          % (mo[0], mo[1], mo[2]))
    print("  that same floor FULL tape : %+0.4f/window  se %.4f  t %+0.2f"
          % (full[0], full[1], full[2]))
    base_o = mean_on("0.51", slugs[cut:])
    print("  the shipped floor 0.51 OOS: %+0.4f/window  se %.4f  t %+0.2f"
          % (base_o[0], base_o[1], base_o[2]))
    print()
    best_out = max(FLOORS, key=lambda lo: mean_on(lo, slugs[cut:])[0])
    print("  best floor OUT of sample  : %s" % best_out)
    print("  same as the in-sample pick: %s"
          % ("YES" if best_out == best_in else "NO"))
    print()

    # ---- is the OOS block simply a kinder regime for EVERY arm?
    pos = sum(1 for lo in FLOORS if mean_on(lo, slugs[cut:])[0] > 0)
    pos_in = sum(1 for lo in FLOORS if mean_on(lo, slugs[:cut])[0] > 0)
    print("  arms positive IN sample   : %d of %d" % (pos_in, len(FLOORS)))
    print("  arms positive OUT of sample: %d of %d" % (pos, len(FLOORS)))
    print()

    # ---- THE DECIDING TEST. A real edge does not change sign between
    # halves. A parameter that is SIGNIFICANTLY NEGATIVE in one half and
    # SIGNIFICANTLY POSITIVE in the other is unstable, and the full-sample
    # figure is the honest summary of it.
    flipped = (mi[0] < 0) != (mo[0] < 0)
    both_strong = abs(mi[2]) > 2.0 and abs(mo[2]) > 2.0
    print("=" * 88)
    print("VERDICT")
    print("=" * 88)
    if flipped and both_strong:
        print("  NOT AN EDGE -- UNSTABLE. Floor %s is significantly NEGATIVE"
              % best_in)
        print("  in sample (%+0.4f, t %+0.2f) and significantly POSITIVE out"
              % (mi[0], mi[2]))
        print("  of sample (%+0.4f, t %+0.2f). A real edge does not change"
              % (mo[0], mo[2]))
        print("  sign at 2 sigma on both sides. Over the FULL tape it is")
        print("  %+0.4f a window (t %+0.2f)." % (full[0], full[2]))
        print()
        print("  The in-sample pick also failed to transfer: the best floor")
        print("  out of sample was %s, not %s." % (best_out, best_in))
        print("  And %d of %d arms are positive out of sample against %d of %d"
              % (pos, len(FLOORS), pos_in, len(FLOORS)))
        print("  in sample, so the later block is a kinder REGIME rather")
        print("  than any floor being good. Promoting this would be fitting")
        print("  a 63-window run.")
    elif full[0] > 0 and full[2] > 2.0 and not flipped:
        print("  CREDIBLE. Floor %s is positive on the full tape (%+0.4f,"
              % (best_in, full[0]))
        print("  t %+0.2f) and does not change sign between halves. It still"
              % full[2])
        print("  needs an INDEPENDENT tape, because this one chose it.")
    else:
        print("  NOT PROMOTABLE. Full tape %+0.4f a window (t %+0.2f)."
              % (full[0], full[2]))
        print("  The per-price table was selection on noise: it conditions")
        print("  on the price a fill happened at, most volume sits in the")
        print("  cheap buckets, and skipping a cheap favorite does not hand")
        print("  you its expensive fill later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

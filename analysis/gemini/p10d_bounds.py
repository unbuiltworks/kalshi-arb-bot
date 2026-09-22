"""P10-D: a BOUND on the historical arms, not another point estimate.

    python3 analysis/gemini/p10d_bounds.py analysis/gemini/p7_bulk_tape.json

The historical corpus has no books, so its coverage under the P10 decoder is
ZERO: not one row can be classified. Reporting a "decoded-only" number for it
would mean reporting an empty set. What can be done instead is to run the arms
under each hypothesis applied to EVERY row, which brackets whatever the truth
is.

THE TRANSFORM. If a printed row is in the complement's space (H2), then in the
contract's space it is

    price  ->  1 - price
    type   ->  the other one

because buying NO at p is selling YES at 1-p, and the aggressor that lifted the
NO ask lifted it by hitting the YES bid. The venue's own definition of `type`
("buy means an ask was removed by an incoming buy order") is what makes the
side flip as well as the price.

    ALL_H1   every row read as printed        -- what P7/P8/P9 assumed
    ALL_H2   every row read as its complement -- the opposite extreme
    MIXED_k  each row independently H2 with probability k, several seeds --
             the interior, to show whether the two ends bracket smoothly or
             the result is unstable in between

No row is silently included: each arm states which assumption produced it.
Nothing here is a candidate, a threshold or a tuning step.
"""
import json
import os
import random
import sys
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import p8_audit as A                                           # noqa: E402
import p9d_report as R                                         # noqa: E402
import p9d_sequential as S                                     # noqa: E402

FLIP = {"buy": "sell", "sell": "buy"}


def transform(prints, mode, rng=None, p_h2=0.5):
    out = []
    for row in prints:
        r = list(row)
        h2 = (mode == "ALL_H2" or
              (mode == "MIXED" and rng is not None and rng.random() < p_h2))
        if h2:
            r[1] = FLIP.get(str(r[1]), str(r[1]))
            r[2] = str(F(1) - F(str(r[2])))
        out.append(r)
    return out


def arms(contracts, spread, split, mode, seed=0, p_h2=0.5):
    rng = random.Random(seed)
    cs = []
    for c in contracts:
        if A.split_of(c["day"]) != split or not c.get("prints"):
            continue
        d = dict(c)
        d["prints"] = transform(S.prints_of(c), mode, rng, p_h2)
        cs.append(d)
    out = {}
    for arm, skew in (("S", F(0)), ("A0", F(0)), ("A3", F(2, 100))):
        rows = R.run_arm(cs, spread=spread, arm=arm, skew=skew, comp_max=F(1))
        out[arm] = R.score(rows)
    return out


def main(path):
    raw = json.load(open(path))
    lanes = defaultdict(list)
    for c in raw:
        ln = A.lane(c)
        if ln in ("5m", "15m") and c.get("prints"):
            lanes[ln].append(c)
    SPREAD = {"5m": F(28, 100), "15m": F(6, 100)}

    print("P10-D BOUND -- the same arms under each reading of the tape")
    print("coverage of the P10 decoder on this corpus: 0% (it carries no books)")
    print(f"\n{'lane':>5} {'split':>5} {'assumption':>12} {'arm':>4} "
          f"{'paired':>7} {'matched$':>9} {'orphan$':>9} {'NET$':>9} {'RoC':>8}")
    for lane in ("5m", "15m"):
        for split in ("dev", "val"):
            rows = []
            for mode, seed in (("ALL_H1", 0), ("ALL_H2", 0),
                               ("MIXED", 1), ("MIXED", 2), ("MIXED", 3)):
                res = arms(lanes[lane], SPREAD[lane], split, mode, seed=seed)
                tag = mode if mode != "MIXED" else f"MIXED s{seed}"
                for arm, s in res.items():
                    if not s:
                        continue
                    rows.append((tag, arm, s))
                    roc = "-" if s["roc"] is None else f"{s['roc']:.2%}"
                    print(f"{lane:>5} {split:>5} {tag:>12} {arm:>4} "
                          f"{s['two_sided_fill_rate']:>6.1%} {s['matched']:>9.2f} "
                          f"{s['residual']:>9.2f} {s['net']:>9.2f} {roc:>8}",
                          flush=True)
            for arm in ("S", "A0", "A3"):
                nets = [s["net"] for t, a, s in rows if a == arm]
                if nets:
                    print(f"{lane:>5} {split:>5} {'BOUND':>12} {arm:>4} "
                          f"{'':>7} {'':>9} {'':>9} "
                          f"[{min(nets):.2f}, {max(nets):.2f}]")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         "analysis/gemini/p7_bulk_tape.json")

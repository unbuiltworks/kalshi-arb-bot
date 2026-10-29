"""P9-B: is later completion predictable from what is knowable at OPEN?

    python3 analysis/gemini/p9b_predictors.py analysis/gemini/p7_bulk_tape.json

Simple and interpretable on purpose. First a univariate lift table per feature
-- quintiles, completion rate, and the single-feature AUC -- then one small
logistic regression fitted on DEV with standardized inputs so its coefficients
can be read directly. Validation is scored ONCE and nothing is tuned on it.

The target is COMPLETION AVAILABILITY, never settlement direction.

No numpy here; the whole point is a model small enough to read.
"""
import math
import os
import sys
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import p9a_targets as A                                        # noqa: E402

#: every feature is computed from prints[0..i] and the contract's published
#: definition. A feature that needed a later print would be leakage and there
#: is no path for one to get here -- `completion_labels` builds features and
#: labels in separate functions.
FEATURES = (
    ("price", lambda r: float(r["price"])),
    ("dist_50", lambda r: float(r["dist_50"])),
    ("tte_s", lambda r: r["tte_s"]),
    ("elapsed_s", lambda r: r["elapsed_s"]),
    ("n_prints", lambda r: float(r["n_prints"])),
    ("n_opposite", lambda r: float(r["n_opposite"])),
    ("frac_opposite", lambda r: float(r["frac_opposite"])),
    ("ever_opposite", lambda r: 1.0 if r["ever_opposite"] else 0.0),
    ("since_opposite_s", lambda r: (float(r["since_opposite_s"])
                                    if r["since_opposite_s"] is not None
                                    else 9999.0)),
    ("trades_per_min", lambda r: float(r["trades_per_min"])),
    ("dollars_per_min", lambda r: float(r["dollars_per_min"])),
    ("abs_move_5", lambda r: (abs(float(r["move_5"]))
                              if r["move_5"] is not None else 0.0)),
    ("abs_move_all", lambda r: (abs(float(r["move_all"]))
                                if r["move_all"] is not None else 0.0)),
    ("reversals", lambda r: float(r["reversals"])),
    ("prior_two_sided", lambda r: (1.0 if r["prior_two_sided"] else 0.0)),
    ("prior_completion", lambda r: (float(r["prior_completion_rate"])
                                    if r["prior_completion_rate"] is not None
                                    else 0.45)),
    ("hour", lambda r: float(r["hour"])),
)

TARGETS = (("pair<=1.00", lambda r: bool(r["y_pair_le_1.00"])),
           (">=3 @1.00", lambda r: r["y_n_qualifying_1.00"] >= 3),
           ("pair<=0.95", lambda r: bool(r["y_pair_le_0.95"])))


def auc(xs, ys):
    """Rank AUC, ties averaged. 0.5 is no information."""
    pairs = sorted(zip(xs, ys))
    n1 = sum(1 for _x, y in pairs if y)
    n0 = len(pairs) - n1
    if not n1 or not n0:
        return None
    rank = {}
    i = 0
    r = 1
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (r + (r + (j - i))) / 2
        for k in range(i, j + 1):
            rank[k] = avg
        r += (j - i + 1)
        i = j + 1
    s = sum(rank[k] for k, (_x, y) in enumerate(pairs) if y)
    return (s - n1 * (n1 + 1) / 2) / (n1 * n0)


def quintiles(rows, get, tgt, k=5):
    vals = sorted(get(r) for r in rows)
    cuts = [vals[int(i * len(vals) / k)] for i in range(1, k)]

    def bucket(v):
        b = 0
        for c in cuts:
            if v >= c:
                b += 1
        return b
    agg = [[0, 0] for _ in range(k)]
    for r in rows:
        b = bucket(get(r))
        agg[b][0] += 1
        agg[b][1] += int(tgt(r))
    return cuts, agg


def fit_logistic(rows, gets, tgt, *, iters=400, lr=0.5):
    """Plain batch gradient descent on standardized features.

    Standardized so the coefficients are comparable to one another; that is
    the only reason, and it means a coefficient reads as "effect of one
    standard deviation of this feature".
    """
    X = [[g(r) for _n, g in gets] for r in rows]
    y = [1.0 if tgt(r) else 0.0 for r in rows]
    m = len(X[0])
    mu = [sum(row[j] for row in X) / len(X) for j in range(m)]
    sd = []
    for j in range(m):
        v = sum((row[j] - mu[j]) ** 2 for row in X) / len(X)
        sd.append(math.sqrt(v) or 1.0)
    Z = [[(row[j] - mu[j]) / sd[j] for j in range(m)] for row in X]
    w = [0.0] * m
    b = math.log((sum(y) + 1) / (len(y) - sum(y) + 1))
    for _ in range(iters):
        gw = [0.0] * m
        gb = 0.0
        for z, t in zip(Z, y):
            p = 1 / (1 + math.exp(-max(-30, min(30, b + sum(wi * zi for wi, zi
                                                            in zip(w, z))))))
            e = p - t
            gb += e
            for j in range(m):
                gw[j] += e * z[j]
        b -= lr * gb / len(Z)
        for j in range(m):
            w[j] -= lr * gw[j] / len(Z)
    return {"w": w, "b": b, "mu": mu, "sd": sd, "names": [n for n, _g in gets]}


def score(model, rows, gets):
    out = []
    for r in rows:
        z = [(g(r) - mu) / sd for (_n, g), mu, sd
             in zip(gets, model["mu"], model["sd"])]
        out.append(model["b"] + sum(wi * zi for wi, zi in zip(model["w"], z)))
    return out


def main(path):
    opens = A.build(path)
    for lane in ("5m", "15m"):
        dev = [r for r in opens if r["duration"] == lane and r["split"] == "dev"]
        val = [r for r in opens if r["duration"] == lane and r["split"] == "val"]
        print(f"\n{'='*112}\nP9-B  BTC{lane}  DEV n={len(dev)}  "
              f"(validation n={len(val)}, scored once at the end)\n{'='*112}")
        for tname, tgt in TARGETS:
            base = sum(tgt(r) for r in dev) / len(dev)
            print(f"\n  target {tname}   DEV base rate {base:.1%}")
            print(f"    {'feature':>18} {'AUC':>6}   quintile completion rates "
                  f"(low -> high)")
            scored = []
            for fname, get in FEATURES:
                a = auc([get(r) for r in dev], [tgt(r) for r in dev])
                _cuts, agg = quintiles(dev, get, tgt)
                cells = " ".join(f"{(h / n if n else 0):>6.1%}" for n, h in agg)
                scored.append((abs((a or 0.5) - 0.5), fname, a, cells))
            for _k, fname, a, cells in sorted(scored, reverse=True):
                print(f"    {fname:>18} {(a if a is not None else 0.5):>6.3f}   {cells}")

        # one small model, DEV only, on the primary target
        tgt = TARGETS[0][1]
        model = fit_logistic(dev, FEATURES, tgt)
        print(f"\n  logistic on '{TARGETS[0][0]}', fitted on DEV only "
              f"(standardized coefficients)")
        for n, w in sorted(zip(model["names"], model["w"]),
                           key=lambda kv: -abs(kv[1])):
            print(f"    {n:>18} {w:>+7.3f}")
        a_dev = auc(score(model, dev, FEATURES), [tgt(r) for r in dev])
        a_val = auc(score(model, val, FEATURES), [tgt(r) for r in val]) if val else None
        print(f"    AUC dev {a_dev:.3f}"
              + (f"   AUC val {a_val:.3f}  <- scored once, nothing tuned on it"
                 if a_val else ""))


if __name__ == "__main__":
    main(sys.argv[1])

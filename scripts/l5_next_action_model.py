"""What predicts whether L5's next execution is A (same side, new price),
B (same side, same price) or D (opposite side)?

The unconditional split is 43.4 / 23.7 / 32.9. An earlier pass showed residual
MAGNITUDE barely moves it and I wrongly called the mixture "state-independent".
That claim tested ONE variable. This tests every state we can reconstruct from
existing evidence WITHOUT inventing book data, and asks whether the apparent fixed
mixture survives conditioning.

INPUT: analysis/l5/requote/DECISION_TAPE.jsonl (117,683 actions, 1,008 markets),
preserved from 4ca8cad2. No new collection. No engine change.

MARKET LEVEL NORMALISATION. Up and Down prices are complements, so a single
comparable series is needed: `lvl = px` for an up action and `1 - px` for a down
action. All movement features are computed on `lvl`, in up-terms.

Chronological train/test: markets are split by window epoch, earliest 75% train,
latest 25% test. Nothing is optimised on the test markets.

    python3 scripts/l5_next_action_model.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter, defaultdict
from decimal import Decimal as D

TAPE = "analysis/l5/requote/DECISION_TAPE.jsonl"
OUT = "analysis/l5/requote"
CLASSES = ("A_SAME_SIDE_NEW_PRICE", "B_SAME_SIDE_SAME_PRICE", "D_OPPOSITE_SIDE")
SHORT = {"A_SAME_SIDE_NEW_PRICE": "A", "B_SAME_SIDE_SAME_PRICE": "B",
         "D_OPPOSITE_SIDE": "D"}
WINDOW_S = 900.0


def epoch_of(slug: str):
    tail = slug.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def bucket(v, edges):
    """Label a numeric into a bucket, or UNK when it is absent."""
    if v is None:
        return "UNK"
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return f"[{lo},{hi})"
    return f">={edges[-1]}"


def load():
    """Group the tape by market, preserving order, and attach features."""
    mkts = defaultdict(list)
    with open(TAPE) as fh:
        for line in fh:
            r = json.loads(line)
            if not r.get("next_action"):
                continue
            mkts[r["slug"]].append(r)
    rows = []
    for slug, acts in mkts.items():
        ep = epoch_of(slug)
        lvls = []           # (t, lvl) history for movement features
        run = 0
        prev_side = None
        prev_size = None
        for a in acts:
            px = float(a["px"])
            side = a["side"]
            lvl = px if side == "up" else 1.0 - px
            t = float(a["t"])
            run = run + 1 if side == prev_side else 1
            size = float(a["size"])

            def mv(sec):
                """Change in market level over the last `sec` seconds, or None
                when no action is old enough to measure it."""
                old = [l for (tt, l) in lvls if t - tt <= sec]
                return (lvl - old[0]) if old else None

            resid = float(a["inv_after"]["residual"])
            feat = {
                "prev_same_side": ("UNK" if prev_side is None
                                   else str(prev_side == side)),
                "run_len": bucket(run, [1, 2, 3, 5, 9, 17]),
                "lvl_band": bucket(lvl, [0.0, .10, .25, .40, .60, .75, .90]),
                "d_px_same_side": bucket(
                    None if a["d_px_vs_prev_same_side"] is None
                    else float(a["d_px_vs_prev_same_side"]),
                    [-1.0, -.05, -.02, -.005, .005, .02, .05]),
                "same_side_cheaper": (
                    "UNK" if a["d_px_vs_prev_same_side"] is None else
                    str(float(a["d_px_vs_prev_same_side"]) < 0)),
                "mv1": bucket(mv(1), [-1, -.03, -.01, .01, .03]),
                "mv2": bucket(mv(2), [-1, -.03, -.01, .01, .03]),
                "mv5": bucket(mv(5), [-1, -.05, -.01, .01, .05]),
                "mv10": bucket(mv(10), [-1, -.05, -.01, .01, .05]),
                "mv30": bucket(mv(30), [-1, -.08, -.02, .02, .08]),
                "gap_s": bucket(a["gap_s"], [0, 1, 2, 4, 9, 30]),
                "t_remaining": (
                    "UNK" if ep is None else
                    bucket(WINDOW_S - (t - ep), [0, 60, 180, 360, 600, 900])),
                "residual_signed": bucket(resid, [-1e9, -200, -50, -5, 5, 50, 200]),
                "residual_dir": ("flat" if resid == 0
                                 else ("long_up" if resid > 0 else "long_down")),
                "matched": bucket(float(a["inv_after"]["matched"]),
                                  [0, 10, 50, 200, 800]),
                "size": bucket(size, [0, 5, 25, 100, 400]),
                "d_size": bucket(None if prev_size is None else size - prev_size,
                                 [-1e9, -50, -5, 5, 50]),
                "side": side,
                "fragments": bucket(a["fragments"], [1, 2, 3, 6]),
            }
            rows.append({"slug": slug, "epoch": ep, "t": t,
                         "y": SHORT[a["next_action"]["klass"]], "f": feat})
            lvls.append((t, lvl))
            lvls = lvls[-400:]
            prev_side, prev_size = side, size
    return rows


# ------------------------------------------------------------ conditional tables

def dist(rows):
    c = Counter(r["y"] for r in rows)
    n = max(1, len(rows))
    return {k: c[k] / n for k in "ABD"}, len(rows)


def tv(p, q):
    """Total variation distance between two class distributions."""
    return 0.5 * sum(abs(p[k] - q[k]) for k in "ABD")


def conditional_report(rows, base, fh):
    """How much does each variable actually move P(A), P(B), P(D)?"""
    lines = []
    feats = sorted(rows[0]["f"].keys())
    ranked = []
    for name in feats:
        by = defaultdict(list)
        for r in rows:
            by[r["f"][name]].append(r)
        # weighted mean TV distance from the unconditional baseline
        wtv = sum(len(v) * tv(dist(v)[0], base) for v in by.values()) / len(rows)
        spread = 0.0
        big = [(k, v) for k, v in by.items() if len(v) >= 500]
        if len(big) >= 2:
            for cls in "ABD":
                vals = [dist(v)[0][cls] for _, v in big]
                spread = max(spread, max(vals) - min(vals))
        ranked.append((wtv, spread, name, by))
    ranked.sort(reverse=True)
    print("\n" + "=" * 104, file=fh)
    print("WHICH VARIABLES ACTUALLY MOVE P(A)/P(B)/P(D)", file=fh)
    print("  weighted TV = mean total-variation distance from the "
          "unconditional split; spread = widest swing in any single class "
          "across buckets with n >= 500", file=fh)
    print("=" * 104, file=fh)
    print("  %-22s%12s%12s" % ("variable", "weightedTV", "maxSpread"),
          file=fh)
    for wtv, spread, name, _ in ranked:
        print("  %-22s%12.4f%12.3f" % (name, wtv, spread), file=fh)

    print("\n" + "=" * 104, file=fh)
    print("TOP VARIABLES IN DETAIL", file=fh)
    print("=" * 104, file=fh)
    for wtv, spread, name, by in ranked[:7]:
        print(f"\n  {name}   (weighted TV {wtv:.4f}, max spread {spread:.3f})",
              file=fh)
        print("    %-18s%9s%8s%8s%8s" % ("bucket", "n", "P(A)", "P(B)",
                                         "P(D)"), file=fh)
        for k in sorted(by, key=lambda k: -len(by[k])):
            p, n = dist(by[k])
            if n < 200:
                continue
            print("    %-18s%9d%8.3f%8.3f%8.3f" % (k, n, p["A"], p["B"],
                                                   p["D"]), file=fh)
    return [name for _, _, name, _ in ranked]


# ------------------------------------------------------------------- tiny CART

def entropy(rows):
    _, n = dist(rows)
    if n == 0:
        return 0.0
    c = Counter(r["y"] for r in rows)
    out = 0.0
    for k in c:
        p = c[k] / n
        out -= p * math.log(p)
    return out


def grow(rows, feats, depth, min_leaf, used=()):
    node = {"n": len(rows), "p": dist(rows)[0]}
    if depth == 0 or len(rows) < 2 * min_leaf:
        return node
    best = None
    for name in feats:
        if name in used:
            continue
        by = defaultdict(list)
        for r in rows:
            by[r["f"][name]].append(r)
        parts = [v for v in by.values() if len(v) >= min_leaf]
        if len(parts) < 2:
            continue
        kept = sum(len(v) for v in parts)
        gain = entropy(rows) - sum(len(v) / kept * entropy(v) for v in parts)
        if best is None or gain > best[0]:
            best = (gain, name, by)
    if best is None or best[0] <= 1e-5:
        return node
    gain, name, by = best
    node["split"] = name
    node["gain"] = gain
    node["kids"] = {k: grow(v, feats, depth - 1, min_leaf, used + (name,))
                    for k, v in by.items() if len(v) >= min_leaf}
    node["fallback"] = dist(rows)[0]
    return node


def predict(node, f):
    while "split" in node:
        k = f.get(node["split"])
        kid = node["kids"].get(k)
        if kid is None:
            return node["fallback"]
        node = kid
    return node["p"]


def score(rows, predictor, label, fh):
    ll = 0.0
    hit = 0
    for r in rows:
        p = predictor(r["f"])
        ll -= math.log(max(1e-9, p[r["y"]]))
        if max(p, key=p.get) == r["y"]:
            hit += 1
    n = len(rows)
    print("  %-34s log loss %.5f   accuracy %.4f   (n=%d)" % (
        label, ll / n, hit / n, n), file=fh)
    return ll / n, hit / n


# ----------------------------------------------------------------- bursts

def bursts(fh):
    mkts = defaultdict(list)
    with open(TAPE) as f:
        for line in f:
            r = json.loads(line)
            mkts[r["slug"]].append(r)
    out = {0: Counter(), 1: Counter(), 2: Counter()}
    multi_px = {0: 0, 1: 0, 2: 0}
    total = 0
    for acts in mkts.values():
        acts.sort(key=lambda a: a["t"])
        for w in (0, 1, 2):
            i = 0
            while i < len(acts):
                j = i
                grp = [acts[i]]
                while j + 1 < len(acts) and acts[j + 1]["t"] - acts[i]["t"] <= w:
                    j += 1
                    grp.append(acts[j])
                pxs = {a["px"] for a in grp}
                sides = {a["side"] for a in grp}
                out[w][len(grp)] += 1
                if len(pxs) > 1:
                    multi_px[w] += 1
                    if w == 0:
                        out[0]["_distinct_px_same_second"] += len(pxs)
                        if len(sides) > 1:
                            out[0]["_both_sides_same_second"] += 1
                i = j + 1
            if w == 0:
                total += len(acts)
    print("\n" + "=" * 104, file=fh)
    print("BURSTS: multiple L5 fills at DIFFERENT prices inside 0s / 1s / 2s",
          file=fh)
    print("  Fill-only evidence. CONSISTENT with pre-existing ladders being "
          "swept, and ALSO with fast sequential decisions. It does NOT establish "
          "resting orders, and no such claim is made.", file=fh)
    print("=" * 104, file=fh)
    for w in (0, 1, 2):
        groups = sum(v for k, v in out[w].items() if isinstance(k, int))
        print("  window %ds: %d groups, %d (%.1f%%) span MORE THAN ONE price"
              % (w, groups, multi_px[w],
                 100.0 * multi_px[w] / max(1, groups)), file=fh)
    print("  same-second groups touching BOTH sides: %d" % (
        out[0].get("_both_sides_same_second", 0)), file=fh)
    sizes = {k: v for k, v in out[0].items() if isinstance(k, int) and k <= 6}
    print("  size of same-second groups: %s" % (
        dict(sorted(sizes.items())),), file=fh)


def main() -> int:
    if not os.path.exists(TAPE):
        print(f"missing {TAPE}")
        return 2
    rows = load()
    rows.sort(key=lambda r: (r["epoch"] or 0, r["t"]))
    eps = sorted({r["epoch"] for r in rows if r["epoch"]})
    cut = eps[int(len(eps) * 0.75)]
    train = [r for r in rows if (r["epoch"] or 0) < cut]
    test = [r for r in rows if (r["epoch"] or 0) >= cut]

    path = os.path.join(OUT, "NEXT_ACTION_MODEL.txt")
    with open(path, "w") as fh:
        print("=" * 104, file=fh)
        print("WHAT PREDICTS L5's NEXT ACTION CLASS?", file=fh)
        print(f"  {len(rows)} labelled actions, {len(eps)} markets", file=fh)
        print(f"  CHRONOLOGICAL split at window epoch {cut}: "
              f"train {len(train)} / test {len(test)}", file=fh)
        print("  Fills only. No book data, no resting-order claims.", file=fh)
        print("=" * 104, file=fh)
        base, _ = dist(train)
        print("\n  unconditional TRAIN split: " + ", ".join(
            "P(%s)=%.4f" % (k, base[k]) for k in "ABD"), file=fh)
        tb, _ = dist(test)
        print("  unconditional TEST  split: " + ", ".join(
            "P(%s)=%.4f" % (k, tb[k]) for k in "ABD"), file=fh)

        order = conditional_report(train, base, fh)

        feats = sorted(rows[0]["f"].keys())
        print("\n" + "=" * 104, file=fh)
        print("OUT-OF-SAMPLE PREDICTION vs THE UNCONDITIONAL BASELINE", file=fh)
        print("=" * 104, file=fh)
        b_ll, b_acc = score(test, lambda f: base,
                            "baseline (fixed 43/24/33)", fh)
        results = []
        for depth in (2, 3, 4, 5):
            tree = grow(train, feats, depth, 400)
            ll, acc = score(test, lambda f, t=tree: predict(t, f),
                            f"decision tree depth {depth}", fh)
            results.append((depth, tree, ll, acc))
        best = min(results, key=lambda r: r[2])
        print("\n  best tree: depth %d, log loss %.5f vs baseline %.5f "
              "(%.2f%% better), accuracy %.4f vs %.4f" % (
                  best[0], best[2], b_ll, 100.0 * (b_ll - best[2]) / b_ll,
                  best[3], b_acc), file=fh)

        def show(node, ind="   ", key="root"):
            p = node["p"]
            head = ("%s%s  n=%d  A%.2f B%.2f D%.2f" %
                    (ind, key, node["n"], p["A"], p["B"], p["D"]))
            print(head + (("   split on %s" % node["split"])
                          if "split" in node else ""), file=fh)
            for k, v in sorted(node.get("kids", {}).items(),
                               key=lambda kv: -kv[1]["n"])[:6]:
                show(v, ind + "   ", f"{node['split']}={k}")
        print("\n" + "=" * 104, file=fh)
        print("THE TREE (top branches by sample size)", file=fh)
        print("=" * 104, file=fh)
        show(best[1])

        bursts(fh)
    print(open(path).read())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

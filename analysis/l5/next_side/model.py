"""Logistic regression, a compact tree, baselines and scoring — no numpy here.

THE BAR IS NOT 50 %. `repeat last side` already scores 69.2 % on this data, so
a model that "beats chance" has demonstrated nothing. Every table below reports
lift against that baseline, and the ablation is nested so each row's gain is
the INCREMENTAL information of one block over the ones above it.

SPLIT BY WINDOW, CHRONOLOGICALLY. Fills inside one window share an inventory
path and a book; splitting them across train and test leaks the answer. Windows
are ordered by time and cut once.
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ----------------------------------------------------------------- scoring

def auc(y: Sequence[float], p: Sequence[float]) -> Optional[float]:
    """Rank-based AUC with ties averaged."""
    pairs = sorted(zip(p, y))
    n = len(pairs)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = r
        i = j + 1
    pos = sum(1 for _, yy in pairs if yy > 0.5)
    neg = n - pos
    if pos == 0 or neg == 0:
        return None
    s = sum(r for r, (_, yy) in zip(ranks, pairs) if yy > 0.5)
    return (s - pos * (pos + 1) / 2.0) / (pos * neg)


def scores(y: Sequence[float], p: Sequence[float],
           thresh: float = 0.5) -> Dict[str, Any]:
    n = len(y)
    if n == 0:
        return {"n": 0}
    pred = [1.0 if q >= thresh else 0.0 for q in p]
    acc = sum(1 for a, b in zip(y, pred) if a == b) / n
    tp = sum(1 for a, b in zip(y, pred) if a == 1 and b == 1)
    fn = sum(1 for a, b in zip(y, pred) if a == 1 and b == 0)
    tn = sum(1 for a, b in zip(y, pred) if a == 0 and b == 0)
    fp = sum(1 for a, b in zip(y, pred) if a == 0 and b == 1)
    tpr = tp / (tp + fn) if (tp + fn) else None
    tnr = tn / (tn + fp) if (tn + fp) else None
    bal = ((tpr + tnr) / 2.0) if (tpr is not None and tnr is not None) else None
    eps = 1e-12
    ll = -sum(a * math.log(max(q, eps)) + (1 - a) * math.log(max(1 - q, eps))
              for a, q in zip(y, p)) / n
    brier = sum((q - a) ** 2 for a, q in zip(y, p)) / n
    return {"n": n, "accuracy": round(acc, 4),
            "balanced_accuracy": (round(bal, 4) if bal is not None else None),
            "auc": (round(auc(y, p), 4) if auc(y, p) is not None else None),
            "log_loss": round(ll, 4), "brier": round(brier, 4)}


def calibration(y: Sequence[float], p: Sequence[float],
                bins: int = 10) -> List[Dict[str, Any]]:
    out = []
    order = sorted(range(len(p)), key=lambda i: p[i])
    per = max(1, len(order) // bins)
    for b in range(bins):
        idx = order[b * per: (b + 1) * per if b < bins - 1 else len(order)]
        if not idx:
            continue
        out.append({"bin": b + 1, "n": len(idx),
                    "mean_predicted": round(sum(p[i] for i in idx) / len(idx), 4),
                    "observed": round(sum(y[i] for i in idx) / len(idx), 4)})
    return out


# ------------------------------------------------------- logistic regression

class Logit:
    """L2 logistic regression, standardized, full-batch gradient descent."""

    def __init__(self, l2: float = 1.0, iters: int = 400, lr: float = 0.5):
        self.l2, self.iters, self.lr = l2, iters, lr
        self.w: List[float] = []
        self.b = 0.0
        self.mu: List[float] = []
        self.sd: List[float] = []

    def _std(self, X: Sequence[Sequence[float]]) -> List[List[float]]:
        return [[(v - m) / s for v, m, s in zip(row, self.mu, self.sd)]
                for row in X]

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[float]) -> "Logit":
        n, d = len(X), len(X[0])
        self.mu = [sum(r[j] for r in X) / n for j in range(d)]
        self.sd = []
        for j in range(d):
            v = sum((r[j] - self.mu[j]) ** 2 for r in X) / n
            self.sd.append(math.sqrt(v) if v > 1e-12 else 1.0)
        Z = self._std(X)
        self.w = [0.0] * d
        self.b = 0.0
        for _ in range(self.iters):
            gw = [0.0] * d
            gb = 0.0
            for row, target in zip(Z, y):
                z = self.b + sum(wj * v for wj, v in zip(self.w, row))
                pr = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z))))
                e = pr - target
                gb += e
                for j, v in enumerate(row):
                    gw[j] += e * v
            self.b -= self.lr * gb / n
            for j in range(d):
                self.w[j] -= self.lr * (gw[j] / n + self.l2 * self.w[j] / n)
        return self

    def predict(self, X: Sequence[Sequence[float]]) -> List[float]:
        out = []
        for row in self._std(X):
            z = self.b + sum(wj * v for wj, v in zip(self.w, row))
            out.append(1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z)))))
        return out

    def coefficients(self, names: Sequence[str]) -> List[Tuple[str, float]]:
        """Standardized coefficients -- comparable across features."""
        return sorted(zip(names, [round(w, 4) for w in self.w]),
                      key=lambda kv: -abs(kv[1]))


# ------------------------------------------------------------- compact tree

class Tree:
    """Depth-limited CART on gini, with a minimum leaf size."""

    def __init__(self, depth: int = 4, min_leaf: int = 200):
        self.depth, self.min_leaf = depth, min_leaf
        self.node: Any = None

    def _build(self, X, y, idx, d):
        p = sum(y[i] for i in idx) / len(idx)
        if d >= self.depth or len(idx) < 2 * self.min_leaf or p in (0.0, 1.0):
            return {"leaf": p, "n": len(idx)}
        best = None
        for j in range(len(X[0])):
            vals = sorted({X[i][j] for i in idx})
            if len(vals) < 2:
                continue
            step = max(1, len(vals) // 12)
            for k in range(step, len(vals), step):
                thr = vals[k]
                li = [i for i in idx if X[i][j] <= thr]
                ri = [i for i in idx if X[i][j] > thr]
                if len(li) < self.min_leaf or len(ri) < self.min_leaf:
                    continue
                pl = sum(y[i] for i in li) / len(li)
                pr = sum(y[i] for i in ri) / len(ri)
                g = (len(li) * 2 * pl * (1 - pl)
                     + len(ri) * 2 * pr * (1 - pr)) / len(idx)
                if best is None or g < best[0]:
                    best = (g, j, thr, li, ri)
        if best is None:
            return {"leaf": p, "n": len(idx)}
        _, j, thr, li, ri = best
        return {"j": j, "thr": thr, "n": len(idx),
                "L": self._build(X, y, li, d + 1),
                "R": self._build(X, y, ri, d + 1)}

    def fit(self, X, y):
        self.node = self._build(X, y, list(range(len(X))), 0)
        return self

    def _one(self, node, row):
        while "leaf" not in node:
            node = node["L"] if row[node["j"]] <= node["thr"] else node["R"]
        return node["leaf"]

    def predict(self, X):
        return [self._one(self.node, r) for r in X]

    def describe(self, names, node=None, d=0) -> List[str]:
        node = self.node if node is None else node
        if "leaf" in node:
            return [f"{'  ' * d}-> p={node['leaf']:.3f}  (n={node['n']})"]
        out = [f"{'  ' * d}{names[node['j']]} <= {node['thr']:.4f}  (n={node['n']})"]
        out += self.describe(names, node["L"], d + 1)
        out += self.describe(names, node["R"], d + 1)
        return out

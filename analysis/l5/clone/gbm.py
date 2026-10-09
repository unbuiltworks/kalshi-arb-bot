"""Histogram multiclass gradient boosting, written out in numpy.

WHY THIS EXISTS. The linear softmax clone reproduced the 70% EXTEND base
rate and nothing else: its argmax never fired COMPLEMENT, even though its
log-loss was materially better than the prior. That is the signature of a
policy that is NONLINEAR in these features -- "complement when the pair is
cheap AND the residual is large AND there is time left" is a conjunction a
linear model in 14 features cannot express. sklearn is not installed, so
the tree learner is written here rather than assumed.

Features are quantile-binned once into uint8, so the whole 760k-row corpus
is a few megabytes and every split search is a bincount.
"""
from __future__ import annotations
import numpy as np


def quantile_bins(X, n_bins=64):
    edges = []
    for j in range(X.shape[1]):
        q = np.unique(np.quantile(X[:, j], np.linspace(0, 1, n_bins + 1)[1:-1]))
        edges.append(q)
    return edges


def binify(X, edges):
    B = np.empty(X.shape, dtype=np.uint8)
    for j, e in enumerate(edges):
        B[:, j] = np.searchsorted(e, X[:, j], side="left")
    return B


class Tree:
    __slots__ = ("feat", "thr_bin", "left", "right", "value")

    def __init__(self):
        self.feat = []; self.thr_bin = []; self.left = []
        self.right = []; self.value = []

    def _new(self):
        self.feat.append(-1); self.thr_bin.append(0); self.left.append(-1)
        self.right.append(-1); self.value.append(0.0)
        return len(self.feat) - 1

    def predict(self, B):
        out = np.zeros(len(B))
        idx = np.zeros(len(B), dtype=np.int64)
        active = np.ones(len(B), dtype=bool)
        feat = np.asarray(self.feat); thr = np.asarray(self.thr_bin)
        left = np.asarray(self.left); right = np.asarray(self.right)
        val = np.asarray(self.value)
        while active.any():
            cur = idx[active]
            leaf = feat[cur] < 0
            rows = np.flatnonzero(active)
            out[rows[leaf]] = val[cur[leaf]]
            active[rows[leaf]] = False
            rows = rows[~leaf]; cur = cur[~leaf]
            if len(rows) == 0:
                break
            go_left = B[rows, feat[cur]] <= thr[cur]
            idx[rows] = np.where(go_left, left[cur], right[cur])
        return out


def _build(B, g, h, n_bins, max_depth, min_leaf, lam):
    """One regression tree on gradients/hessians, depth-first by level."""
    t = Tree()
    root = t._new()
    nodes = [(root, np.arange(len(g)), 0)]
    nf = B.shape[1]
    while nodes:
        node, rows, depth = nodes.pop()
        G, H = g[rows].sum(), h[rows].sum()
        if depth >= max_depth or len(rows) < 2 * min_leaf:
            t.value[node] = -G / (H + lam)
            continue
        best = (0.0, -1, 0)
        parent = G * G / (H + lam)
        for j in range(nf):
            b = B[rows, j]
            gh = np.bincount(b, weights=g[rows], minlength=n_bins)
            hh = np.bincount(b, weights=h[rows], minlength=n_bins)
            ch = np.bincount(b, minlength=n_bins)
            cg = np.cumsum(gh); chh = np.cumsum(hh); cc = np.cumsum(ch)
            gl, hl, nl = cg[:-1], chh[:-1], cc[:-1]
            gr, hr = G - gl, H - hl
            nr = len(rows) - nl
            gain = gl * gl / (hl + lam) + gr * gr / (hr + lam) - parent
            gain[(nl < min_leaf) | (nr < min_leaf)] = -np.inf
            k = int(np.argmax(gain))
            if gain[k] > best[0]:
                best = (float(gain[k]), j, k)
        if best[1] < 0:
            t.value[node] = -G / (H + lam)
            continue
        _, j, k = best
        m = B[rows, j] <= k
        l_id, r_id = t._new(), t._new()
        t.feat[node] = j; t.thr_bin[node] = k
        t.left[node] = l_id; t.right[node] = r_id
        nodes.append((l_id, rows[m], depth + 1))
        nodes.append((r_id, rows[~m], depth + 1))
    return t


def fit(B, y, *, n_class=3, rounds=60, lr=0.25, max_depth=6,
        min_leaf=200, lam=1.0, n_bins=64, verbose=False):
    n = len(y)
    F = np.zeros((n, n_class))
    Yh = np.zeros((n, n_class)); Yh[np.arange(n), y] = 1.0
    prior = Yh.mean(axis=0)
    base = np.log(np.clip(prior, 1e-9, None))
    F += base
    trees = []
    for r in range(rounds):
        Z = F - F.max(axis=1, keepdims=True)
        P = np.exp(Z); P /= P.sum(axis=1, keepdims=True)
        row = []
        for k in range(n_class):
            g = (P[:, k] - Yh[:, k])
            h = np.maximum(P[:, k] * (1 - P[:, k]), 1e-6)
            t = _build(B, g, h, n_bins, max_depth, min_leaf, lam)
            F[:, k] += lr * t.predict(B)
            row.append(t)
        trees.append(row)
        if verbose and (r + 1) % 10 == 0:
            p = np.clip(P[np.arange(n), y], 1e-12, 1)
            print("    round %3d train_logloss=%.4f" % (r + 1,
                                                        -np.log(p).mean()),
                  flush=True)
    return {"base": base, "trees": trees, "lr": lr, "n_class": n_class}


def predict_proba(model, B):
    F = np.tile(model["base"], (len(B), 1)).astype(np.float64)
    for row in model["trees"]:
        for k, t in enumerate(row):
            F[:, k] += model["lr"] * t.predict(B)
    Z = F - F.max(axis=1, keepdims=True)
    P = np.exp(Z)
    return P / P.sum(axis=1, keepdims=True)


def gain_importance(model, n_feat):
    imp = np.zeros(n_feat)
    for row in model["trees"]:
        for t in row:
            for f in t.feat:
                if f >= 0:
                    imp[f] += 1.0
    return imp / max(1.0, imp.sum())

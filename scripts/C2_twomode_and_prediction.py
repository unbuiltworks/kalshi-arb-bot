"""Items 3 and 4 -- the two-mode behaviour, and out-of-sample prediction.

ITEM 3. Do the late aggressive (taker) fills help or hurt, and do they
shrink or grow the directional residual?

Two measurements, both on the 72,550 fills carrying an A1 execution label:

  (a) RESIDUAL EFFECT. For every labelled fill, the change in residual
      quantity it caused. A fill that reduces |residual| is compressing;
      one that grows it is accumulating directional risk.
  (b) COUNTERFACTUAL P&L. Re-settle each window twice -- once as it
      happened, once with the TAKER fills removed -- and compare. Removing
      a buy changes the inventory path, so the counterfactual is replayed
      from scratch with FIFO pairing, not patched.

The counterfactual is honest about one thing it cannot know: whether the
maker fills would still have arrived in a world where L5 did not aggress.
It assumes they would. That is an assumption, stated, not a result.

ITEM 4. Does execution mode improve out-of-sample prediction of the next
action, against the flat-hazard null?

A logistic model predicts "does the next fill switch leg", fitted on the
first 70% of windows chronologically and scored on the last 30% -- so the
test set is strictly later in time and no window is split. Three nested
feature sets, compared on out-of-sample log loss and AUC against the
constant-rate baseline:

  M0  constant hazard only (the null that survived four rounds)
  M1  + inventory and clock features
  M2  + execution-mode features (is this fill taker, size, price)

If M2 does not beat M0 out of sample, the flat-hazard finding stands and
the two-mode structure, though real, carries no predictive information
about the NEXT side. Those are different claims and the distinction is the
point of this item.

Read-only. No network. Pure stdlib plus the dataset.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import gzip
import json
import math
import os
import random
import statistics
import sys
from collections import deque

EPS = 1e-9
SEED = 20261009


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def ci(vals, rng, reps=1500, cap=20000):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None, None
    k, m = len(v), min(len(v), cap)
    out = sorted(statistics.fmean([v[rng.randrange(k)] for _ in range(m)])
                 for _ in range(reps))
    return out[int(reps * .025)], out[int(reps * .975)]


def fifo_take(q, qty):
    sp = 0.0
    while qty > EPS and q:
        lot = q[0]
        use = min(lot[0], qty)
        sp += use * lot[1]
        lot[0] -= use
        qty -= use
        if lot[0] <= EPS:
            q.popleft()
    return sp


def replay(fills):
    """fills: list of (leg, price, size). Returns (matched, spend_matched,
    resid_side, resid_qty, total_spend)."""
    q = {"UP": deque(), "DOWN": deque()}
    mq = ms = sp = 0.0
    for leg, px, sz in fills:
        if px is None or sz is None:
            continue
        q[leg].append([sz, px])
        sp += sz * px
        pair = min(sum(l[0] for l in q["UP"]), sum(l[0] for l in q["DOWN"]))
        if pair > EPS:
            ms += fifo_take(q["UP"], pair) + fifo_take(q["DOWN"], pair)
            mq += pair
    uq = sum(l[0] for l in q["UP"])
    dq = sum(l[0] for l in q["DOWN"])
    if uq > EPS:
        rs, rq = "Up", uq
    elif dq > EPS:
        rs, rq = "Down", dq
    else:
        rs, rq = "", 0.0
    return mq, ms, rs, rq, sp


def settle(mq, rs, rq, sp, winner):
    proceeds = mq + (rq if (rs and winner and rs.lower() == winner.lower())
                     else 0.0)
    return proceeds - sp


# Vectorised: a pure-Python fit over ~740k rows x 400 iterations does not
# terminate in usable time. Same model and objective, numpy arithmetic.
# Non-intercept columns are standardised so a single learning rate suits
# features on very different scales (log size vs fraction-of-window).
def logistic(X, y, iters=400, lr=2.0, l2=1e-4):
    import numpy as np
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n, d = X.shape
    mu = X[:, 1:].mean(axis=0) if d > 1 else np.zeros(0)
    sd = X[:, 1:].std(axis=0) if d > 1 else np.zeros(0)
    if d > 1:
        sd[sd == 0] = 1.0
    Z = X.copy()
    if d > 1:
        Z[:, 1:] = (X[:, 1:] - mu) / sd
    w = np.zeros(d)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -30, 30)))
        w -= lr * (Z.T @ (p - y) / n + l2 * w)
    return (w, mu, sd)


def predict_all(model, X):
    import numpy as np
    w, mu, sd = model
    X = np.asarray(X, dtype=np.float64)
    Z = X.copy()
    if X.shape[1] > 1:
        Z[:, 1:] = (X[:, 1:] - mu) / sd
    return 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -30, 30)))


def logloss(ps, ys):
    import numpy as np
    p = np.clip(np.asarray(ps, dtype=np.float64), 1e-9, 1 - 1e-9)
    y = np.asarray(ys, dtype=np.float64)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def auc(ps, ys):
    import numpy as np
    p = np.asarray(ps, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    pos, neg = y.sum(), len(y) - y.sum()
    if not pos or not neg:
        return None
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=np.float64)
    ranks[order] = np.arange(1, len(p) + 1)
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rng = random.Random(SEED)

    lab = {}
    for r in csv.DictReader(gzip.open(a.labels, "rt")):
        lab[(r["event_slug"], r["trade_seq"])] = r["classification"]

    wins = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(
            a.dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                wins[r["event_slug"]].append(r)

    R = {}

    # ---------------- ITEM 3 ----------------
    resid_delta = collections.defaultdict(list)
    cf = []
    cf_resid = []
    n_win_with_taker = 0
    taker_fills = 0
    for slug, rows in wins.items():
        rows.sort(key=lambda r: int(r["trade_seq"]))
        labs = [lab.get((slug, r["trade_seq"])) for r in rows]
        if not any(labs):
            continue
        # (a) residual effect per labelled fill
        prev_rq = 0.0
        for r, c in zip(rows, labs):
            rq = f(r["residual_qty"], 0.0) or 0.0
            if c:
                resid_delta[c].append(rq - prev_rq)
            prev_rq = rq
        # (b) counterfactual: drop TAKER_OBSERVED fills
        if not any(c == "TAKER_OBSERVED" for c in labs):
            continue
        n_win_with_taker += 1
        winner = rows[-1]["settled_outcome"]
        if not winner:
            continue
        actual = [(r["leg"], f(r["price"]), f(r["size"])) for r in rows]
        noTk = [(r["leg"], f(r["price"]), f(r["size"]))
                for r, c in zip(rows, labs) if c != "TAKER_OBSERVED"]
        taker_fills += sum(1 for c in labs if c == "TAKER_OBSERVED")
        ma = replay(actual)
        mn = replay(noTk)
        pa = settle(ma[0], ma[2], ma[3], ma[4], winner)
        pn = settle(mn[0], mn[2], mn[3], mn[4], winner)
        cf.append(pa - pn)
        cf_resid.append(ma[3] - mn[3])

    lo, hi = ci(cf, rng)
    rlo, rhi = ci(cf_resid, rng)
    R["item3_two_mode"] = {
        "residual_effect_per_fill": {
            "_what": "change in residual quantity caused by the fill. "
                     "Negative = compressing, positive = accumulating "
                     "directional risk.",
            **{c: {"n": len(v),
                   "mean_delta_residual": round(statistics.fmean(v), 4),
                   "share_reducing_residual": round(
                       sum(1 for x in v if x < 0) / len(v), 4)}
               for c, v in sorted(resid_delta.items()) if v}},
        "counterfactual_remove_taker_fills": {
            "_what": "each window replayed twice with FIFO pairing -- as it "
                     "happened, and with TAKER_OBSERVED fills removed. "
                     "Positive means the aggressive fills ADDED P&L.",
            "_assumption": "that the maker fills would still have arrived "
                           "had L5 not aggressed. Stated, not demonstrated.",
            "windows": len(cf), "taker_fills_removed": taker_fills,
            "mean_pnl_effect_per_window": round(statistics.fmean(cf), 4)
            if cf else None,
            "ci95": [None if lo is None else round(lo, 4),
                     None if hi is None else round(hi, 4)],
            "share_of_windows_improved": round(
                sum(1 for x in cf if x > 0) / len(cf), 4) if cf else None,
            "mean_residual_effect": round(statistics.fmean(cf_resid), 4)
            if cf_resid else None,
            "residual_ci95": [None if rlo is None else round(rlo, 4),
                              None if rhi is None else round(rhi, 4)],
            "VERDICT_pnl": ("AGGRESSIVE FILLS ADD P&L" if lo and lo > 0 else
                            ("AGGRESSIVE FILLS DESTROY P&L"
                             if hi and hi < 0 else "INDETERMINATE")),
            "VERDICT_residual": (
                "they INCREASE the directional residual" if rlo and rlo > 0
                else ("they REDUCE the directional residual"
                      if rhi and rhi < 0 else "INDETERMINATE")),
        },
    }

    # ---------------- ITEM 4 ----------------
    slugs = sorted(wins, key=lambda s: int(wins[s][0]["timestamp"]))
    cut = int(len(slugs) * 0.7)
    tr_s, te_s = set(slugs[:cut]), set(slugs[cut:])
    feats = {"M0": [], "M1": [], "M2": []}
    ys = {"train": [], "test": []}
    Xs = {m: {"train": [], "test": []} for m in feats}
    for slug in slugs:
        rows = sorted(wins[slug], key=lambda r: int(r["trade_seq"]))
        split = "train" if slug in tr_s else "test"
        for i in range(len(rows) - 1):
            cur, nxt = rows[i], rows[i + 1]
            y = int(nxt["leg_switched_from_prev"] or 0)
            rq = f(cur["residual_qty"], 0.0) or 0.0
            rv = f(cur["residual_vwap"]) or 0.5
            tts = f(cur["seconds_to_settlement"]) or 0.0
            dur = f(cur["duration_minutes"]) or 15.0
            px = f(cur["price"]) or 0.5
            sz = f(cur["size"]) or 0.0
            istk = 1.0 if lab.get((slug, cur["trade_seq"])) == \
                "TAKER_OBSERVED" else 0.0
            m0 = [1.0]
            m1 = m0 + [math.log1p(rq), rv, tts / (dur * 60.0),
                       1.0 if cur["leg"] == "UP" else 0.0]
            m2 = m1 + [istk, math.log1p(sz), px]
            Xs["M0"][split].append(m0)
            Xs["M1"][split].append(m1)
            Xs["M2"][split].append(m2)
            ys[split].append(y)
    out = {}
    for m in ("M0", "M1", "M2"):
        model = logistic(Xs[m]["train"], ys["train"])
        ps = predict_all(model, Xs[m]["test"])
        out[m] = {"n_train": len(ys["train"]), "n_test": len(ys["test"]),
                  "oos_logloss": round(logloss(ps, ys["test"]), 6),
                  "oos_auc": round(auc(ps, ys["test"]), 5)
                  if auc(ps, ys["test"]) is not None else None}
    base = out["M0"]["oos_logloss"]
    R["item4_prediction"] = {
        "_design": "chronological 70/30 split by window; no window spans "
                   "the boundary; the test set is strictly later in time.",
        "models": {
            "M0": "constant hazard (intercept only)",
            "M1": "+ log residual qty, residual VWAP, fraction of window "
                  "remaining, current leg",
            "M2": "+ is_taker, log size, price",
        },
        "results": out,
        "logloss_improvement_vs_M0": {
            m: round(base - out[m]["oos_logloss"], 6) for m in ("M1", "M2")},
        "VERDICT":
            ("execution-mode features DO improve out-of-sample prediction"
             if out["M2"]["oos_logloss"] < base - 1e-4 else
             "execution-mode features do NOT improve out-of-sample "
             "prediction of the next side; the flat-hazard null stands"),
        "_reconciliation":
            "A flat hazard and a real two-mode structure are compatible. "
            "Mode governs the PRICE, SIZE and TIMING of a fill. The flat "
            "hazard governs WHICH LEG comes next. These are different "
            "questions, and this item tests only the second.",
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps(R, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Behavioral clone of L5's next observable action, from public fills only.

WHAT IS BEING PREDICTED. At every one of L5's own fills, the label is the
NEXT observable action in the same market: EXTEND (next fill on the same
side), COMPLEMENT (opposite side), STOP (no further fill in this market).
The label alphabet is relative to the side just traded, so the model learns
a policy over inventory, not a directional view.

WHAT IS NOT BEING PREDICTED. Nothing here claims to know an order that
never filled. Timing, price and size of the NEXT fill are regressed as
secondary targets and reported as fill-conditional, which is strictly
weaker than order placement: an unfilled quote leaves no trace in this
corpus and no feature here stands in for one.

HONESTY OF THE SPLITS. Two held-out sets, reported separately:
  * BY MARKET  -- every decision point of a held-out conditionId is unseen,
    so a market's own idiosyncrasy cannot leak from train to test;
  * BY TIME    -- the last 20% of each regime's calendar span, which is the
    only split that answers "would this have worked going forward".
The two regimes (pre/post 2026-06-23) are fit and scored independently;
nothing is pooled across the break.

BASELINES. Accuracy against a 70% majority class is meaningless on its own,
so every model number is reported beside: the majority-class rule, the
always-EXTEND rule, the prior-matching stratified guess, and a naive pair
bot ("complement when residual inventory exists, else extend") which is the
simplified pair-completion policy this clone is supposed to beat.
"""
from __future__ import annotations
import json, sys
import numpy as np

LABELS = ["EXTEND", "COMPLEMENT", "STOP"]
FEATS = ["imbalance", "matched_frac", "residual_shares", "pair_cost_sofar",
         "price", "price_is_favourite", "n_fills", "log_size", "t_frac",
         "tte_s", "gap_prev_s", "dur_5m", "same_side_run", "opp_has_any"]
K = 3


# ---------------------------------------------------------------- model ---
def fit_softmax(X, y, *, l2=1e-4, iters=400, lr=0.5, seed=0):
    """Multinomial logistic regression, full-batch, Nesterov-free Adam.

    sklearn is not installed in this environment; this is the same model,
    written out, so the result is auditable rather than opaque.
    """
    n, d = X.shape
    W = np.zeros((d + 1, K))
    Xb = np.hstack([X, np.ones((n, 1))])
    Yh = np.zeros((n, K)); Yh[np.arange(n), y] = 1.0
    m = np.zeros_like(W); v = np.zeros_like(W)
    b1, b2, eps = 0.9, 0.999, 1e-8
    for t in range(1, iters + 1):
        Z = Xb @ W
        Z -= Z.max(axis=1, keepdims=True)
        P = np.exp(Z); P /= P.sum(axis=1, keepdims=True)
        G = Xb.T @ (P - Yh) / n
        G[:-1] += l2 * W[:-1]
        m = b1 * m + (1 - b1) * G
        v = b2 * v + (1 - b2) * G * G
        W -= lr * (m / (1 - b1 ** t)) / (np.sqrt(v / (1 - b2 ** t)) + eps)
    return W


def predict_proba(W, X):
    Z = np.hstack([X, np.ones((len(X), 1))]) @ W
    Z -= Z.max(axis=1, keepdims=True)
    P = np.exp(Z)
    return P / P.sum(axis=1, keepdims=True)


# --------------------------------------------------------------- scoring --
def score(y, pred, proba=None):
    out = {"n": int(len(y)),
           "accuracy": float((pred == y).mean()) if len(y) else None}
    per = {}
    f1s = []
    for k in range(K):
        tp = int(((pred == k) & (y == k)).sum())
        fp = int(((pred == k) & (y != k)).sum())
        fn = int(((pred != k) & (y == k)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        f1s.append(f1)
        per[LABELS[k]] = {"support": int((y == k).sum()), "predicted":
                          int((pred == k).sum()), "precision": round(prec, 4),
                          "recall": round(rec, 4), "f1": round(f1, 4)}
    out["per_class"] = per
    out["macro_f1"] = round(float(np.mean(f1s)), 4)
    out["confusion_true_by_pred"] = [[int(((y == a) & (pred == b)).sum())
                                      for b in range(K)] for a in range(K)]
    if proba is not None and len(y):
        p = np.clip(proba[np.arange(len(y)), y], 1e-12, 1.0)
        out["log_loss"] = round(float(-np.log(p).mean()), 4)
    return out


def baselines(Xtr, ytr, Xte, yte, cols):
    maj = int(np.bincount(ytr, minlength=K).argmax())
    prior = np.bincount(ytr, minlength=K) / len(ytr)
    rng = np.random.default_rng(0)
    resid = Xte[:, cols["residual_shares"]]
    opp = Xte[:, cols["opp_has_any"]]
    pair_bot = np.where((resid > 0) & (opp == 0), 1,
                        np.where(resid > 0, 1, 0)).astype(np.int64)
    return {
        "majority_class_%s" % LABELS[maj]: score(yte, np.full(len(yte), maj)),
        "always_EXTEND": score(yte, np.zeros(len(yte), dtype=np.int64)),
        "stratified_prior": score(yte, rng.choice(K, size=len(yte), p=prior)),
        "naive_pair_bot": score(yte, pair_bot),
    }


# ------------------------------------------------------- secondary heads --
def ols(X, y, l2=1e-3):
    Xb = np.hstack([X, np.ones((len(X), 1))])
    A = Xb.T @ Xb + l2 * np.eye(Xb.shape[1])
    return np.linalg.solve(A, Xb.T @ y)


def reg_report(Xtr, ytr, Xte, yte, *, name, unit):
    w = ols(Xtr, ytr)
    p = np.hstack([Xte, np.ones((len(Xte), 1))]) @ w
    const = float(np.median(ytr))
    sse = float(((yte - p) ** 2).sum())
    sst = float(((yte - yte.mean()) ** 2).sum())
    return {"target": name, "unit": unit, "n_train": int(len(ytr)),
            "n_test": int(len(yte)),
            "median_abs_err": round(float(np.median(np.abs(yte - p))), 4),
            "baseline_median_abs_err":
                round(float(np.median(np.abs(yte - const))), 4),
            "mean_abs_err": round(float(np.abs(yte - p).mean()), 4),
            "r2_out_of_sample": round(1.0 - sse / sst, 4) if sst else None}


# ------------------------------------------------------------------ main --
def main() -> int:
    d = np.load("analysis/l5/clone/decisions.npz", allow_pickle=True)
    X, Y, META, NXT, CID = d["X"], d["Y"], d["META"], d["NXT"], d["CID"]
    ts, early = META[:, 0], META[:, 1].astype(bool)
    cols = {f: i for i, f in enumerate(FEATS)}

    # market held-out assignment is a stable hash of the conditionId, so
    # the same markets are held out on every rerun and across regimes.
    mkt_bucket = np.asarray([int(c[-6:], 16) % 5 if len(c) >= 6 else 0
                             for c in CID])

    report = {"decision_points": int(len(Y)),
              "label_base_rates": {LABELS[k]: round(float((Y == k).mean()), 4)
                                   for k in range(K)},
              "regimes": {}}

    for rname, mask in (("early_pre_2026_06_23", early),
                        ("late_post_2026_06_23", ~early)):
        Xr, Yr, tr_ts = X[mask], Y[mask], ts[mask]
        bk = mkt_bucket[mask]
        Nr = NXT[mask]
        mu, sd = Xr.mean(axis=0), Xr.std(axis=0)
        sd[sd == 0] = 1.0
        Z = (Xr - mu) / sd

        reg = {"n": int(len(Yr)),
               "span_utc": [int(tr_ts.min()), int(tr_ts.max())],
               "label_base_rates": {LABELS[k]: round(float((Yr == k).mean()), 4)
                                    for k in range(K)},
               "splits": {}}

        splits = {}
        te_m = bk == 0
        splits["held_out_markets_20pct"] = (~te_m, te_m)
        cut = np.quantile(tr_ts, 0.8)
        te_t = tr_ts >= cut
        splits["held_out_trailing_time_20pct"] = (~te_t, te_t)

        for sname, (tr, te) in splits.items():
            if te.sum() == 0 or tr.sum() == 0:
                continue
            W = fit_softmax(Z[tr], Yr[tr])
            P = predict_proba(W, Z[te])
            pred = P.argmax(axis=1)
            entry = {"n_train": int(tr.sum()), "n_test": int(te.sum()),
                     "clone": score(Yr[te], pred, P),
                     "baselines": baselines(Z[tr], Yr[tr], Z[te], Yr[te],
                                            cols)}
            # secondary heads, fill-conditional, on acted rows only
            acted = np.isfinite(Nr[:, 0])
            for j, (nm, un) in enumerate([("next_gap_s", "seconds"),
                                          ("next_price", "usd_per_share"),
                                          ("next_size", "shares")]):
                a_tr, a_te = tr & acted, te & acted
                yv_tr, yv_te = Nr[a_tr, j], Nr[a_te, j]
                if j == 0:
                    yv_tr, yv_te = np.log1p(yv_tr), np.log1p(yv_te)
                    un = "log1p(seconds)"
                if j == 2:
                    yv_tr, yv_te = np.log1p(yv_tr), np.log1p(yv_te)
                    un = "log1p(shares)"
                entry.setdefault("secondary_heads", []).append(
                    reg_report(Z[a_tr], yv_tr, Z[a_te], yv_te,
                               name=nm, unit=un))
            # coefficients, standardized, for behavioral reading
            entry["standardized_coefficients"] = {
                LABELS[k]: {f: round(float(W[i, k]), 3)
                            for i, f in enumerate(FEATS)} for k in range(K)}
            reg["splits"][sname] = entry
        report["regimes"][rname] = reg

    with open("analysis/l5/clone/clone_eval.json", "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)

    # ---- terse human summary -------------------------------------------
    for rname, reg in report["regimes"].items():
        print("\n== %s  n=%d" % (rname, reg["n"]))
        print("   base rates:", reg["label_base_rates"])
        for sname, e in reg["splits"].items():
            c = e["clone"]
            print("   -- %s  train=%d test=%d" % (sname, e["n_train"],
                                                  e["n_test"]))
            print("      clone acc=%.4f macroF1=%.4f logloss=%s"
                  % (c["accuracy"], c["macro_f1"], c.get("log_loss")))
            for bn, b in e["baselines"].items():
                print("      base  %-28s acc=%.4f macroF1=%.4f"
                      % (bn, b["accuracy"], b["macro_f1"]))
            for k in LABELS:
                pc = c["per_class"][k]
                print("      %-11s prec=%.3f rec=%.3f f1=%.3f n=%d"
                      % (k, pc["precision"], pc["recall"], pc["f1"],
                         pc["support"]))
            for h in e["secondary_heads"]:
                print("      head %-12s MAE=%.3f median=%.3f "
                      "(const baseline %.3f) R2oos=%s"
                      % (h["target"], h["mean_abs_err"],
                         h["median_abs_err"],
                         h["baseline_median_abs_err"],
                         h["r2_out_of_sample"]))
    print("\nwrote analysis/l5/clone/clone_eval.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The clone, evaluated properly: boosted trees against four baselines.

Same splits, same regimes, same label alphabet as `train.py`. The only
change is model capacity, which is the one thing the linear result left
open. Argmax accuracy is reported, but so is the ranking quality of
P(COMPLEMENT) -- a policy that knows WHEN L5 completes is useful even if
its argmax is dominated by a 70% base rate, and AUC says that where
accuracy cannot.
"""
from __future__ import annotations
import json, sys
import numpy as np

from analysis.l5.clone import gbm
from analysis.l5.clone.train import (LABELS, FEATS, K, score, baselines,
                                     reg_report)


def auc(y_true_bin, s):
    n1 = int(y_true_bin.sum()); n0 = len(y_true_bin) - n1
    if n1 == 0 or n0 == 0:
        return None
    r = np.empty(len(s), dtype=np.float64)
    order = np.argsort(s, kind="mergesort")
    sv = s[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((r[y_true_bin.astype(bool)].sum() - n1 * (n1 + 1) / 2.0)
                 / (n0 * n1))


def prior_logloss(y_tr, y_te):
    p = np.clip(np.bincount(y_tr, minlength=K) / len(y_tr), 1e-12, 1)
    return float(-np.log(p[y_te]).mean())


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    d = np.load("analysis/l5/clone/decisions.npz", allow_pickle=True)
    X, Y, META, NXT, CID = d["X"], d["Y"], d["META"], d["NXT"], d["CID"]
    ts, early = META[:, 0], META[:, 1].astype(bool)
    bucket = np.asarray([int(c[-6:], 16) % 5 if len(c) >= 6 else 0
                         for c in CID])
    cols = {f: i for i, f in enumerate(FEATS)}
    report = {"model": "histogram multiclass GBDT, numpy",
              "rounds": rounds, "regimes": {}}

    for rname, mask in (("early_pre_2026_06_23", early),
                        ("late_post_2026_06_23", ~early)):
        Xr, Yr, tsr, bk, Nr = X[mask], Y[mask], ts[mask], bucket[mask], NXT[mask]
        edges = gbm.quantile_bins(Xr)
        B = gbm.binify(Xr, edges)
        mu, sd = Xr.mean(axis=0), Xr.std(axis=0); sd[sd == 0] = 1.0
        Z = (Xr - mu) / sd
        reg = {"n": int(len(Yr)), "splits": {}}
        cut = np.quantile(tsr, 0.8)
        for sname, te in (("held_out_markets_20pct", bk == 0),
                          ("held_out_trailing_time_20pct", tsr >= cut)):
            tr = ~te
            print("[%s/%s] train=%d test=%d" % (rname, sname, tr.sum(),
                                                te.sum()), flush=True)
            m = gbm.fit(B[tr], Yr[tr], rounds=rounds, verbose=True)
            P = gbm.predict_proba(m, B[te])
            pred = P.argmax(axis=1)
            e = {"n_train": int(tr.sum()), "n_test": int(te.sum()),
                 "clone": score(Yr[te], pred, P),
                 "prior_log_loss": round(prior_logloss(Yr[tr], Yr[te]), 4),
                 "auc_vs_rest": {LABELS[k]: (lambda a: round(a, 4)
                                             if a is not None else None)(
                                     auc((Yr[te] == k).astype(np.int64),
                                         P[:, k])) for k in range(K)},
                 "baselines": baselines(Z[tr], Yr[tr], Z[te], Yr[te], cols),
                 "feature_split_share": {f: round(float(v), 4) for f, v in
                                         zip(FEATS, gbm.gain_importance(
                                             m, len(FEATS)))}}
            # threshold sweep: at what P(COMPLEMENT) is a complete call
            # worth making, and how much of L5's completions does it catch
            sweep = []
            for thr in (0.3, 0.35, 0.4, 0.45, 0.5):
                sel = P[:, 1] >= thr
                if sel.sum() == 0:
                    continue
                sweep.append({"threshold": thr,
                              "fired_frac": round(float(sel.mean()), 4),
                              "precision": round(
                                  float((Yr[te][sel] == 1).mean()), 4),
                              "recall": round(float(
                                  ((Yr[te] == 1) & sel).sum()
                                  / max(1, (Yr[te] == 1).sum())), 4)})
            e["complement_threshold_sweep"] = sweep
            acted = np.isfinite(Nr[:, 0])
            heads = []
            for j, (nm, un, lg) in enumerate([("next_gap_s", "seconds", True),
                                              ("next_price", "usd", False),
                                              ("next_size", "shares", True)]):
                a_tr, a_te = tr & acted, te & acted
                yv_tr, yv_te = Nr[a_tr, j], Nr[a_te, j]
                if lg:
                    yv_tr, yv_te = np.log1p(yv_tr), np.log1p(yv_te)
                    un = "log1p(%s)" % un
                heads.append(reg_report(Z[a_tr], yv_tr, Z[a_te], yv_te,
                                        name=nm, unit=un))
            e["secondary_heads_linear"] = heads
            reg["splits"][sname] = e
        report["regimes"][rname] = reg

    with open("analysis/l5/clone/clone_eval_gbm.json", "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)

    for rname, reg in report["regimes"].items():
        print("\n== %s n=%d" % (rname, reg["n"]))
        for sname, e in reg["splits"].items():
            c = e["clone"]
            print("  -- %s" % sname)
            print("     clone acc=%.4f macroF1=%.4f logloss=%.4f "
                  "(prior %.4f)" % (c["accuracy"], c["macro_f1"],
                                    c["log_loss"], e["prior_log_loss"]))
            print("     AUC vs rest:", e["auc_vs_rest"])
            for bn, b in e["baselines"].items():
                print("     base %-28s acc=%.4f macroF1=%.4f"
                      % (bn, b["accuracy"], b["macro_f1"]))
            for k in LABELS:
                pc = c["per_class"][k]
                print("     %-11s prec=%.3f rec=%.3f f1=%.3f n=%d"
                      % (k, pc["precision"], pc["recall"], pc["f1"],
                         pc["support"]))
            print("     COMPLEMENT sweep:", e["complement_threshold_sweep"])
            top = sorted(e["feature_split_share"].items(),
                         key=lambda kv: -kv[1])[:6]
            print("     top features:", top)
    print("\nwrote analysis/l5/clone/clone_eval_gbm.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Fit one deployable model per regime on ALL of that regime's decision
points, and persist it with its binning edges.

Evaluation already happened in `train_gbm.py` on held-out markets and a
held-out trailing time period. This script exists only to produce the
artifact a shadow run loads, so it deliberately does NOT print any
accuracy: a number computed on the data a model was fit on is not
evidence, and emitting one here would invite exactly that mistake.
"""
from __future__ import annotations
import os, pickle, json
import numpy as np

from analysis.l5.clone import gbm
from analysis.l5.clone.train import FEATS, LABELS

OUT = "analysis/l5/clone/models"


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    d = np.load("analysis/l5/clone/decisions.npz", allow_pickle=True)
    X, Y, META = d["X"], d["Y"], d["META"]
    early = META[:, 1].astype(bool)
    manifest = {"features": FEATS, "labels": LABELS,
                "regime_break_utc": "2026-06-23T00:00:00Z",
                "fit_on": "ALL decision points in the regime (no holdout)",
                "evaluation_lives_in": "analysis/l5/clone/clone_eval_gbm.json",
                "regimes": {}}
    for name, mask in (("early", early), ("late", ~early)):
        Xr, Yr = X[mask], Y[mask]
        edges = gbm.quantile_bins(Xr)
        B = gbm.binify(Xr, edges)
        model = gbm.fit(B, Yr, rounds=60, verbose=True)
        path = os.path.join(OUT, "l5_policy_%s.pkl" % name)
        with open(path, "wb") as fh:
            pickle.dump({"model": model, "edges": edges, "features": FEATS,
                         "labels": LABELS, "n_fit": int(len(Yr))}, fh)
        manifest["regimes"][name] = {"path": path, "n_fit": int(len(Yr)),
                                     "label_base_rates": {
                                         LABELS[k]: round(float((Yr == k).mean()), 4)
                                         for k in range(3)}}
        print("wrote", path, "n=", len(Yr), flush=True)
    with open(os.path.join(OUT, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

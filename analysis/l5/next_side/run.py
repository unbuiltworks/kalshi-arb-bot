"""Baselines, chronological OOS, ablation, and CONTINUE-vs-SWITCH.

THE FREEZE CRITERION IS NOT 50 %. `repeat last side` scores 0.692 here. Arm I
is justified only if a model MATERIALLY beats that out of sample; if it does
not, that is the result and it gets reported rather than engineered around.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Sequence

from analysis.l5.inventory_trajectory.trajectory import load
from analysis.l5.next_side import features as F
from analysis.l5.next_side import model as M

TRAIN_FRAC = 0.70


def split_by_window(rows: Sequence[dict], frac: float = TRAIN_FRAC):
    """Chronological, cut at a WINDOW boundary.

    Fills inside one window share an inventory path; splitting them across
    train and test leaks the label. The cut is on windows ordered by their
    first fill's time, never on individual rows.
    """
    first: Dict[str, float] = {}
    for r in rows:
        t = r["t"] or 0
        if r["slug"] not in first or t < first[r["slug"]]:
            first[r["slug"]] = t
    order = sorted(first, key=lambda s: first[s])
    cut = order[:int(len(order) * frac)]
    tr = set(cut)
    return ([r for r in rows if r["slug"] in tr],
            [r for r in rows if r["slug"] not in tr])


def month_split(rows: Sequence[dict]):
    """Spring (Mar+Apr) -> August. A true regime transfer, not a random cut."""
    import datetime as dt
    tr, te = [], []
    for r in rows:
        m = dt.datetime.utcfromtimestamp(r["t"]).strftime("%Y-%m")
        (tr if m in ("2026-03", "2026-04") else te).append(r)
    return tr, te


# ------------------------------------------------------------- baselines

def baselines(rows: Sequence[dict], target: str) -> Dict[str, Any]:
    y = [r[target] for r in rows]
    base = sum(y) / len(y) if rows else 0.0
    out: Dict[str, Any] = {}

    def add(name, p):
        out[name] = M.scores(y, p)

    if target == "y_up":
        add("majority_side", [1.0 if base >= 0.5 else 0.0] * len(rows))
        add("repeat_last_side", [r["is_up"] for r in rows])
        add("buy_cheaper_outcome", [r["cheaper_is_up"] for r in rows])
        # repair = buy the side we hold LESS of
        add("repair_imbalance",
            [1.0 if r["signed_imbalance"] < 0 else 0.0 for r in rows])
    else:
        add("always_continue", [1.0] * len(rows))
        add("always_switch", [0.0] * len(rows))
        add("base_rate_constant", [base] * len(rows))
    return out


# ------------------------------------------------------------- experiment

def design(rows: Sequence[dict], names: Sequence[str]):
    return [[r[n] for n in names] for r in rows]


def fit_eval(tr, te, names, target) -> Dict[str, Any]:
    Xtr, ytr = design(tr, names), [r[target] for r in tr]
    Xte, yte = design(te, names), [r[target] for r in te]
    lg = M.Logit().fit(Xtr, ytr)
    p = lg.predict(Xte)
    return {"logit": M.scores(yte, p),
            "top_coefficients": lg.coefficients(names)[:8],
            "_p": p, "_y": yte, "_model": lg}


def ablation(tr, te, target) -> List[Dict[str, Any]]:
    out = []
    used: List[str] = []
    prev = None
    for block in F.LADDER:
        used.append(block)
        names = F.feature_names(used)
        r = fit_eval(tr, te, names, target)
        row = {"blocks": " + ".join(used), "n_features": len(names),
               **r["logit"]}
        if prev is not None:
            row["delta_accuracy_vs_previous"] = round(
                row["accuracy"] - prev, 4)
        prev = row["accuracy"]
        out.append(row)
    return out


def control_vs_flow(tr, te, target) -> Dict[str, Any]:
    """Which family carries the information: L5's own state, or incoming flow?

    H1/H2 predict his own inventory matters. H3 predicts the market's arrival
    process does and his inventory does not. Fitting each family ALONE, and
    then each family added to last-side, separates them.
    """
    out: Dict[str, Any] = {}
    fams = {"control_only": list(F.CONTROL_BLOCKS),
            "flow_only": list(F.FLOW_BLOCKS),
            "clock_only": list(F.CLOCK_BLOCKS),
            "last_side_plus_control": ["last_side"] + [b for b in F.CONTROL_BLOCKS
                                                       if b != "last_side"],
            "last_side_plus_flow": ["last_side"] + list(F.FLOW_BLOCKS),
            "everything": list(F.LADDER)}
    for name, blocks in fams.items():
        names = F.feature_names(blocks)
        r = fit_eval(tr, te, names, target)
        out[name] = {"blocks": " + ".join(blocks), "n_features": len(names),
                     **r["logit"]}
    return out


def slices(rows, p, y, target) -> Dict[str, Any]:
    def bucket(r):
        return {
            "tte": ("<120s" if r["_tte"] < 120 else
                    "120-450s" if r["_tte"] < 450 else "450s+"),
            "price": ("<0.30" if r["_price"] < 0.30 else
                      "0.30-0.70" if r["_price"] <= 0.70 else ">0.70"),
            "imbalance": ("|imb|<0.05" if abs(r["_imb"]) < 0.05 else
                          "0.05-0.20" if abs(r["_imb"]) < 0.20 else ">=0.20"),
            "run_len": ("1" if r["_run"] == 1 else "2-3" if r["_run"] <= 3
                        else "4-7" if r["_run"] <= 7 else "8+"),
        }
    out: Dict[str, Any] = {}
    for dim in ("tte", "price", "imbalance", "run_len"):
        groups: Dict[str, List[int]] = {}
        for i, r in enumerate(rows):
            groups.setdefault(bucket(r)[dim], []).append(i)
        out[dim] = {}
        for k, idx in sorted(groups.items()):
            yy = [y[i] for i in idx]
            pp = [p[i] for i in idx]
            persist = ([rows[i]["is_up"] for i in idx] if target == "y_up"
                       else [1.0] * len(idx))
            out[dim][k] = {**M.scores(yy, pp),
                           "repeat_last_side_accuracy":
                               round(sum(1 for a, b in zip(yy, persist)
                                         if a == b) / len(idx), 4)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", default="data/l5/raw/activity.jsonl")
    ap.add_argument("--out", default="analysis/l5/next_side/report.json")
    a = ap.parse_args(argv)

    rows = F.build(load(a.activity))
    tr, te = split_by_window(rows)
    mtr, mte = month_split(rows)
    names = F.feature_names(F.LADDER)
    rep: Dict[str, Any] = {
        "rows": len(rows), "train": len(tr), "oos": len(te),
        "train_windows": len({r["slug"] for r in tr}),
        "oos_windows": len({r["slug"] for r in te}),
        "split": "chronological, cut at a window boundary; no window spans both",
    }

    for target in ("y_up", "y_continue"):
        base_rate = sum(r[target] for r in te) / len(te)
        full = fit_eval(tr, te, names, target)
        tree = M.Tree().fit(design(tr, names), [r[target] for r in tr])
        tp = tree.predict(design(te, names))
        rep[target] = {
            "oos_base_rate": round(base_rate, 4),
            "baselines_oos": baselines(te, target),
            "logit_full": full["logit"],
            "tree_full": M.scores([r[target] for r in te], tp),
            "tree_structure": tree.describe(names)[:14],
            "top_coefficients": full["top_coefficients"],
            "ablation": ablation(tr, te, target),
            "control_vs_flow": control_vs_flow(tr, te, target),
            "calibration": M.calibration(full["_y"], full["_p"]),
            "slices": slices(te, full["_p"], full["_y"], target),
        }
        mfull = fit_eval(mtr, mte, names, target)
        rep[target]["regime_transfer_spring_to_august"] = {
            "train_rows": len(mtr), "oos_rows": len(mte),
            "logit": mfull["logit"],
            "baselines_oos": baselines(mte, target),
        }

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in rep.items()
                      if k not in ("y_up", "y_continue")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

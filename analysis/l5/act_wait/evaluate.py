"""Chronological OOS scoring for ACT/WAIT and TIMING. Fidelity only.

NOTHING HERE IS OPTIMISED AGAINST SIMULATED PROFIT. There is no P&L in this
module and no threshold is chosen to make money look better; the operating
point is selected on the training split alone and then applied unchanged to the
held-out split.

Metrics are computed from counts in pure Python. PR-AUC is average precision
over the sorted score sweep, which is the interpolation-free definition and the
right one when the positive class is common.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from analysis.l5.act_wait.dataset import Opportunity
from analysis.l5.act_wait import model as M


def confusion(y: Sequence[int], p: Sequence[float], thr: float) -> Dict[str, int]:
    tp = fp = fn = tn = 0
    for yi, pi in zip(y, p):
        act = pi >= thr
        if yi and act:
            tp += 1
        elif yi and not act:
            fn += 1
        elif (not yi) and act:
            fp += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def pr_auc(y: Sequence[int], p: Sequence[float]) -> float:
    """Average precision. Ties are kept together so a constant predictor scores
    its base rate rather than an artefact of sort order."""
    pos = sum(y)
    if not pos:
        return 0.0
    order = sorted(range(len(y)), key=lambda i: -p[i])
    ap, tp, seen, i = 0.0, 0, 0, 0
    while i < len(order):
        j = i
        while j < len(order) and p[order[j]] == p[order[i]]:
            j += 1
        block = order[i:j]
        btp = sum(y[k] for k in block)
        tp += btp
        seen += len(block)
        if btp:
            ap += (tp / seen) * (btp / pos)
        i = j
    return ap


def calibration(y: Sequence[int], p: Sequence[float], bins: int = 10) -> List[dict]:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, pi in enumerate(p)
               if (pi >= lo and pi < hi) or (b == bins - 1 and pi == 1.0)]
        if not idx:
            continue
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(idx),
                    "predicted": round(sum(p[i] for i in idx) / len(idx), 4),
                    "observed": round(sum(y[i] for i in idx) / len(idx), 4)})
    return out


def calibration_error(cal: Sequence[Mapping]) -> float:
    n = sum(c["n"] for c in cal) or 1
    return round(sum(c["n"] * abs(c["predicted"] - c["observed"]) for c in cal) / n, 4)


def pick_threshold(train: Sequence[Opportunity], score: Callable, h: int) -> float:
    """Operating point chosen on TRAIN ONLY, by best F1 over a coarse sweep.

    F1 rather than anything P&L-shaped, and train-only so the held-out numbers
    are not quietly tuned. A coarse grid on purpose: a finely tuned threshold on
    this much data is fitting the grid.
    """
    y = [o.acted_within[h] for o in train]
    p = [score(o) for o in train]
    best, best_f1 = 0.5, -1.0
    for k in range(1, 20):
        thr = k / 20
        c = confusion(y, p, thr)
        prec = c["tp"] / max(1, c["tp"] + c["fp"])
        rec = c["tp"] / max(1, c["tp"] + c["fn"])
        f1 = 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)
        if f1 > best_f1:
            best, best_f1 = thr, f1
    return best


def score_act(name: str, test: Sequence[Opportunity], score: Callable,
              h: int, thr: float) -> dict:
    y = [o.acted_within[h] for o in test]
    p = [score(o) for o in test]
    c = confusion(y, p, thr)
    n = len(y) or 1
    prec = c["tp"] / max(1, c["tp"] + c["fp"])
    rec = c["tp"] / max(1, c["tp"] + c["fn"])
    cal = calibration(y, p)
    return {
        "model": name, "horizon_s": h, "n": len(y), "threshold": thr,
        "base_action_rate": round(sum(y) / n, 4),
        "predicted_action_rate": round((c["tp"] + c["fp"]) / n, 4),
        "precision": round(prec, 4), "recall": round(rec, 4),
        "f1": round(0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec), 4),
        "pr_auc": round(pr_auc(y, p), 4),
        # false ACTION = we act where L5 did not; missed ACTION = L5 acted and
        # we sat. Both as a share of ALL opportunities, so they are comparable
        # with the action rate above rather than with each other's denominator.
        "false_action_rate": round(c["fp"] / n, 4),
        "missed_action_rate": round(c["fn"] / n, 4),
        "calibration_error": calibration_error(cal),
        "calibration": cal,
        **c,
    }


def by_slice(test: Sequence[Opportunity], score: Callable, h: int, thr: float,
             keyfn: Callable[[Opportunity], str], min_n: int = 200) -> List[dict]:
    """Per-slice results where the sample supports them; small slices are
    dropped rather than reported as noise."""
    groups: Dict[str, List[Opportunity]] = {}
    for o in test:
        groups.setdefault(keyfn(o), []).append(o)
    out = []
    for k, rows in sorted(groups.items()):
        if len(rows) < min_n:
            continue
        r = score_act(k, rows, score, h, thr)
        out.append({"slice": k, "n": r["n"],
                    "base": r["base_action_rate"], "pred": r["predicted_action_rate"],
                    "precision": r["precision"], "recall": r["recall"],
                    "pr_auc": r["pr_auc"], "false_action": r["false_action_rate"],
                    "missed_action": r["missed_action_rate"]})
    return out


def score_timing(test: Sequence[Opportunity], tm: M.TimingModel) -> dict:
    correct, n = 0, 0
    tiers: Dict[str, int] = {}
    conf: Dict[str, Dict[str, int]] = {}
    abs_err: List[float] = []
    for o in test:
        truth = M.gap_bucket(o.next_gap_s)
        pred, dist, tier = tm.predict(o)
        tiers[tier] = tiers.get(tier, 0) + 1
        conf.setdefault(truth, {})
        conf[truth][pred] = conf[truth].get(pred, 0) + 1
        correct += int(pred == truth)
        n += 1
        if o.next_gap_s is not None:
            mid = {"gap<2s": 1.0, "gap2-10": 6.0, "gap10-30": 20.0, "gap>30": 60.0}
            abs_err.append(abs(mid[pred] - o.next_gap_s))
    modal = max(tm.marg, key=tm.marg.get) if tm.marg else None
    base = sum(1 for o in test if M.gap_bucket(o.next_gap_s) == modal) / max(1, n)
    abs_err.sort()
    return {
        "n": n,
        "bucket_accuracy": round(correct / max(1, n), 4),
        "modal_baseline_accuracy": round(base, 4),
        "lift_over_modal": round(correct / max(1, n) - base, 4),
        "median_abs_gap_error_s": abs_err[len(abs_err) // 2] if abs_err else None,
        "p90_abs_gap_error_s": abs_err[int(.9 * len(abs_err))] if abs_err else None,
        "lookup_tiers": tiers,
        "confusion_truth_x_pred": conf,
    }


def tier_rates(rows: Sequence[Opportunity], am_model: M.ActModel) -> dict:
    t: Dict[str, int] = {}
    for o in rows:
        _p, tier = am_model.predict(o)
        t[tier] = t.get(tier, 0) + 1
    n = len(rows) or 1
    return {k: round(v / n, 4) for k, v in sorted(t.items())}

"""P0: L5's risk as FRACTIONS. P2: does anything beat persistence at the
boundary between same-side runs?

P0 -- WHY FRACTIONS AND NOT $195. Copying an absolute residual tolerance
would import his balance sheet rather than his policy. If his envelope is
fractional -- "never let more than X of the book go unhedged" -- it scales
to any capital base and is the thing worth replicating. So every risk
measure is reported as a RATIO as well as a level, conditioned on
time-to-expiry bands.

P2 -- THE RIGHT TARGET. Raw side accuracy is dominated by the class
distribution: 71.9% of decisions continue the current side, so "do what
you did last time" scores 75.3% while explaining nothing about the only
interesting event. The question is the TRANSITION: what ends a run?
So switch is scored as its own class with precision, recall, F1 and
balanced accuracy, and probabilistic forecasts are scored with Brier and
log loss, where a model that is merely confident in the majority class is
penalised.

FEATURE FAMILIES, added incrementally on the SAME window-level split:
  A  inventory only (qty, cost, imbalance, book pair cost, residual)
  B  A + the prediction-market price path L5's OWN fills reveal
  C  A + B + BTC path / distance from strike      -- DATA ABSENT, see P1
  E  full order book and flow                     -- DATA ABSENT for these
                                                     windows, see P1

C and E are declared, not silently skipped: the module reports them as
blocked with the reason, so the table shows what was tested and what
could not be.

NO FROZEN GEMINI PARAMETER IS READ OR WRITTEN. The frozen policy remains
the control and is not imported.

Accounting label: historical observation of a third party.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.gemini_execution.l5_behavior import (DOWN, SAME_SIDE, SWITCH_SIDE,
                                              UP, reconstruct,
                                              window_summary)
from scripts.l5_vs_frozen_entry_study import L5_TAPE, load_l5

LABEL = "L5_RISK_AND_SWITCH_MODEL_V1"
TTE_BANDS = ((0.0, 300.0, "0-300s"), (300.0, 600.0, "300-600s"),
             (600.0, 900.1, "600-900s"))


def _st(xs: Sequence[float]) -> Dict[str, Any]:
    xs = [x for x in xs if x is not None and x == x]
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "min": round(s[0], 4),
            "p25": round(s[len(s) // 4], 4),
            "median": round(statistics.median(s), 4),
            "p75": round(s[3 * len(s) // 4], 4),
            "p90": round(s[9 * len(s) // 10], 4),
            "max": round(s[-1], 4), "mean": round(statistics.fmean(s), 4)}


def _band(v: Optional[float], bands) -> Optional[str]:
    if v is None:
        return None
    for lo, hi, name in bands:
        if lo <= v < hi:
            return name
    return None


# ============================================================= P0: risk
def risk_fractions(steps_by_win: Dict[str, List[Dict[str, Any]]]
                   ) -> Dict[str, Any]:
    """Risk as RATIOS, per window and per decision, by TTE band."""
    per_window: List[Dict[str, Any]] = []
    for w, steps in steps_by_win.items():
        if not steps:
            continue
        sm = window_summary(steps)
        total_shares = sm["up_qty"] + sm["down_qty"]
        deployed = sm["dollars_deployed"]
        peak_res_prin = sm["peak_residual_principal"]
        peak_imb = sm["peak_imbalance_shares"]
        per_window.append({
            "window": w,
            "peak_residual_principal_over_dollars_deployed": (
                peak_res_prin / deployed if deployed else None),
            "peak_residual_shares_over_total_shares": (
                peak_imb / total_shares if total_shares else None),
            "peak_imbalance_over_final_matched": (
                peak_imb / sm["matched_qty"] if sm["matched_qty"] else None),
            "final_residual_over_total_inventory": (
                sm["residual_qty"] / total_shares if total_shares else None),
            "dollars_deployed": deployed,
            "peak_residual_principal": peak_res_prin,
            "final_book_pair_cost": sm["final_book_pair_cost"],
            "n_fills": sm["n_fills"],
        })

    keys = ("peak_residual_principal_over_dollars_deployed",
            "peak_residual_shares_over_total_shares",
            "peak_imbalance_over_final_matched",
            "final_residual_over_total_inventory")
    whole = {k: _st([r[k] for r in per_window]) for k in keys}

    # the SAME ratios measured at every decision, bucketed by the TTE at
    # which that state existed -- so "does he run hotter early or late?"
    by_tte: Dict[str, Dict[str, Any]] = {}
    for _lo, _hi, name in TTE_BANDS:
        rows = [s["state"] for steps in steps_by_win.values()
                for s in steps
                if _band(s["state"]["tte"], TTE_BANDS) == name]
        tot = [(r["up_qty"] + r["down_qty"]) for r in rows]
        by_tte[name] = {
            "n_decisions": len(rows),
            "unhedged_fraction_abs": _st(
                [abs(r["imbalance_frac"] or 0.0) for r in rows]),
            "residual_principal_usd": _st(
                [r["residual_principal"] for r in rows]),
            "residual_principal_over_book_cost": _st(
                [(r["residual_principal"] / (r["up_cost"] + r["down_cost"]))
                 for r in rows if (r["up_cost"] + r["down_cost"])]),
            "residual_shares_over_total_shares": _st(
                [(r["residual_qty"] / t) for r, t in zip(rows, tot) if t]),
            "book_pair_cost": _st([r["book_pair_cost"] for r in rows]),
        }
    return {
        "per_window_ratios": whole,
        "by_tte_band": by_tte,
        "n_windows": len(per_window),
        "interpretation_guide": (
            "a FRACTIONAL envelope shows up as a ratio that is stable "
            "across windows and TTE bands while the dollar level varies "
            "with window size. An ABSOLUTE envelope shows up as the "
            "opposite. Compare the spread of the ratios against the "
            "spread of dollars_deployed."),
        "dollars_deployed": _st([r["dollars_deployed"]
                                 for r in per_window]),
        "stability_check": _stability(per_window),
    }


def _stability(per_window: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Is the RATIO tighter than the DOLLAR level? That is the test."""
    def cv(xs: List[Optional[float]]) -> Optional[float]:
        xs = [x for x in xs if x is not None and x == x]
        if len(xs) < 2:
            return None
        m = statistics.fmean(xs)
        return None if not m else round(statistics.stdev(xs) / m, 4)
    return {
        "cv_dollars_deployed": cv([r["dollars_deployed"]
                                   for r in per_window]),
        "cv_peak_residual_principal_usd": cv(
            [r["peak_residual_principal"] for r in per_window]),
        "cv_peak_residual_over_deployed": cv(
            [r["peak_residual_principal_over_dollars_deployed"]
             for r in per_window]),
        "cv_peak_residual_shares_over_total": cv(
            [r["peak_residual_shares_over_total_shares"]
             for r in per_window]),
        "reading": ("a LOWER coefficient of variation for the ratio than "
                    "for the dollar level is evidence of a fractional "
                    "envelope; the reverse is evidence of an absolute one"),
    }


# ======================================================= P2: the boundary
def runs(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Same-side runs, and the state at the boundary that ended each."""
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for s in steps:
        side = s["action"]["side"]
        if cur is None or side != cur["side"]:
            if cur is not None:
                cur["ended_by_switch"] = True
                cur["boundary_state"] = s["state"]
                out.append(cur)
            cur = {"side": side, "length": 1, "start_tte": s["state"]["tte"],
                   "ended_by_switch": False, "boundary_state": None,
                   "first_price": s["action"]["price"],
                   "dollars": s["action"]["dollars"]}
        else:
            cur["length"] += 1
            cur["dollars"] += s["action"]["dollars"]
            cur["last_price"] = s["action"]["price"]
    if cur is not None:
        out.append(cur)
    return out


def _feat(state: Dict[str, Any], family: str) -> Dict[str, float]:
    """Only the fields that family is allowed to see."""
    f: Dict[str, float] = {
        "abs_imb_frac": abs(state["imbalance_frac"] or 0.0),
        "book_pair": (state["book_pair_cost"]
                      if state["book_pair_cost"] is not None else 0.96),
        "trades_since_switch": float(state["trades_since_switch"]),
        "resid_frac": ((state["residual_qty"]
                        / (state["up_qty"] + state["down_qty"]))
                       if (state["up_qty"] + state["down_qty"]) else 0.0),
    }
    if family in ("B", "D"):
        f.update({
            "print_pair": (state["print_pair_cost"]
                           if state["print_pair_cost"] is not None else 1.0),
            "price_change_same_side": (state["price_change_same_side"] or 0.0),
            "price_change_prev": (state["price_change_since_prev_fill"]
                                  or 0.0),
            "secs_since_prev": (state["seconds_since_prev"] or 0.0),
            "prev_price": (state["prev_price"] or 0.5),
        })
    return f


def fit_logistic(rows: List[Tuple[Dict[str, float], int]], *,
                 iters: int = 400, lr: float = 0.3) -> Dict[str, float]:
    """Plain logistic regression by gradient descent.

    Deliberately simple and inspectable: the deliverable is an
    interpretable rule, and a hand-rolled logit keeps the coefficients
    readable and adds no dependency.
    """
    if not rows:
        return {}
    names = sorted(rows[0][0])
    mu = {k: statistics.fmean([r[0][k] for r in rows]) for k in names}
    sd = {k: (statistics.pstdev([r[0][k] for r in rows]) or 1.0)
          for k in names}
    w = {k: 0.0 for k in names}
    b = 0.0
    n = len(rows)
    for _ in range(iters):
        gw = {k: 0.0 for k in names}
        gb = 0.0
        for x, y in rows:
            z = b + sum(w[k] * (x[k] - mu[k]) / sd[k] for k in names)
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            e = p - y
            for k in names:
                gw[k] += e * (x[k] - mu[k]) / sd[k]
            gb += e
        for k in names:
            w[k] -= lr * gw[k] / n
        b -= lr * gb / n
    out = {"_bias": b}
    out.update({k: w[k] for k in names})
    out.update({"_mu_" + k: mu[k] for k in names})
    out.update({"_sd_" + k: sd[k] for k in names})
    return out


def predict_p(model: Dict[str, float], x: Dict[str, float]) -> float:
    if not model:
        return 0.5
    z = model["_bias"]
    for k in x:
        if k in model:
            z += model[k] * (x[k] - model["_mu_" + k]) / model["_sd_" + k]
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def classify_metrics(y: List[int], p: List[float], *,
                     thr: float = 0.5) -> Dict[str, Any]:
    tp = sum(1 for a, b in zip(y, p) if a == 1 and b >= thr)
    fp = sum(1 for a, b in zip(y, p) if a == 0 and b >= thr)
    fn = sum(1 for a, b in zip(y, p) if a == 1 and b < thr)
    tn = sum(1 for a, b in zip(y, p) if a == 0 and b < thr)
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * prec * rec / (prec + rec)
          if (prec and rec) else (0.0 if (prec is not None
                                          and rec is not None) else None))
    tnr = tn / (tn + fp) if (tn + fp) else None
    same_prec = tn / (tn + fn) if (tn + fn) else None
    brier = statistics.fmean([(b - a) ** 2 for a, b in zip(y, p)]) if y else None
    ll = (-statistics.fmean([a * math.log(max(b, 1e-12))
                            + (1 - a) * math.log(max(1 - b, 1e-12))
                            for a, b in zip(y, p)]) if y else None)
    return {
        "n": len(y), "switch_base_rate_pct": (
            round(100.0 * sum(y) / len(y), 1) if y else None),
        "accuracy_pct": (round(100.0 * (tp + tn) / len(y), 1) if y else None),
        "switch_precision": (None if prec is None else round(prec, 4)),
        "switch_recall": (None if rec is None else round(rec, 4)),
        "switch_f1": (None if f1 is None else round(f1, 4)),
        "same_side_precision": (None if same_prec is None
                                else round(same_prec, 4)),
        "same_side_recall": (None if tnr is None else round(tnr, 4)),
        "balanced_accuracy": (None if (rec is None or tnr is None)
                              else round((rec + tnr) / 2, 4)),
        "brier": (None if brier is None else round(brier, 5)),
        "log_loss": (None if ll is None else round(ll, 5)),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def run(family_key: str = "btc-updown-15m",
        train_frac: float = 0.7) -> Dict[str, Any]:
    by = load_l5(L5_TAPE, family_key)
    steps_by_win = {w: reconstruct(by[w]) for w in sorted(by)}
    steps_by_win = {w: s for w, s in steps_by_win.items() if s}
    wins = sorted(steps_by_win)
    cut = int(len(wins) * train_frac)
    tr_w, te_w = wins[:cut], wins[cut:]

    def pairs(ws, fam):
        out = []
        for w in ws:
            for s in steps_by_win[w]:
                if s["state"]["is_first_fill"]:
                    continue
                y = 1 if s["action"]["side_relation"] == SWITCH_SIDE else 0
                out.append((_feat(s["state"], fam), y))
        return out

    families: Dict[str, Any] = {}
    for fam, desc in (("A", "inventory only"),
                      ("B", "A + L5-fill-derived price path")):
        tr, te = pairs(tr_w, fam), pairs(te_w, fam)
        model = fit_logistic(tr)
        p = [predict_p(model, x) for x, _ in te]
        y = [yy for _, yy in te]
        coef = {k: round(v, 4) for k, v in model.items()
                if not k.startswith("_mu_") and not k.startswith("_sd_")}
        families[fam] = {"description": desc, "coefficients": coef,
                         "out_of_sample": classify_metrics(y, p)}
    # BASELINES, scored the same way so the comparison is like-for-like.
    te_A = pairs(te_w, "A")
    y = [yy for _, yy in te_A]
    base_rate = (statistics.fmean([yy for _, yy in pairs(tr_w, "A")])
                 if tr_w else 0.3)
    families["BASELINE_persistence"] = {
        "description": ("always predict SAME_SIDE -- i.e. p(switch)=0, the "
                        "75.3% rule the inventory model failed to beat"),
        "out_of_sample": classify_metrics(y, [0.0] * len(y))}
    families["BASELINE_train_base_rate"] = {
        "description": ("always predict the TRAIN switch rate (%.3f) as a "
                        "probability -- the honest probabilistic null"
                        % base_rate),
        "out_of_sample": classify_metrics(y, [base_rate] * len(y))}
    families["C_btc_path"] = {
        "description": "A + B + BTC path / distance from strike",
        "status": "BLOCKED -- NO DATA",
        "why": ("no BTC spot or index series covering 2026-09-14/15 exists "
                "in this repository; searched for price/spot/index/klines/"
                "ohlc artifacts and found none"),
    }
    families["E_book_and_flow"] = {
        "description": "full order-book and flow features",
        "status": "BLOCKED -- NO DATA FOR THESE WINDOWS",
        "why": ("the one historical 1-second Polymarket L2 dataset in this "
                "repository's provenance is Zenodo record 22084977 "
                "(updown_15m.parquet), which covers 2026-08-21..24 and has "
                "ZERO overlap with the 2026-09-14/15 behavioral corpus. It "
                "does cover 12 windows in which L5 traded, 3,166 fills, "
                "88.3% as maker -- a usable book-complete sample, but a "
                "DIFFERENT one. The parquet itself is not on disk."),
    }

    # run-length and the boundary
    all_runs = [r for w in wins for r in runs(steps_by_win[w])]
    ended = [r for r in all_runs if r["ended_by_switch"]]
    return {
        "label": LABEL, "family": family_key,
        "n_windows": len(wins),
        "split": {"train_windows": len(tr_w), "test_windows": len(te_w)},
        "P0_risk_fractions": risk_fractions(steps_by_win),
        "P2_switch_models": families,
        "run_length": {
            "n_runs": len(all_runs),
            "length": _st([float(r["length"]) for r in all_runs]),
            "dollars_per_run": _st([r["dollars"] for r in all_runs]),
            "by_side": {
                s: _st([float(r["length"]) for r in all_runs
                        if r["side"] == s]) for s in (UP, DOWN)},
            "at_the_boundary": {
                "abs_imbalance_frac": _st(
                    [abs(r["boundary_state"]["imbalance_frac"] or 0.0)
                     for r in ended]),
                "book_pair_cost": _st(
                    [r["boundary_state"]["book_pair_cost"] for r in ended]),
                "seconds_since_prev": _st(
                    [r["boundary_state"]["seconds_since_prev"]
                     for r in ended]),
                "run_length_that_ended": _st([float(r["length"])
                                              for r in ended]),
            },
        },
        "accounting_label": "historical observation of a third party",
        "control": "the frozen Gemini policy is untouched and not imported",
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="btc-updown-15m")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    r = run(a.family, a.train_frac)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(r, indent=2, sort_keys=True,
                                default=str) + "\n")
    p0 = r["P0_risk_fractions"]
    print("%s  windows=%d  (train %d / test %d)"
          % (r["label"], r["n_windows"], r["split"]["train_windows"],
             r["split"]["test_windows"]))
    print("\nP0  RISK AS FRACTIONS (per window)")
    for k, v in p0["per_window_ratios"].items():
        print("   %-48s %s" % (k, v))
    print("   %-48s %s" % ("dollars_deployed (level, for contrast)",
                           p0["dollars_deployed"]))
    print("\n   STABILITY (coefficient of variation; lower = tighter)")
    for k, v in p0["stability_check"].items():
        if k != "reading":
            print("      %-42s %s" % (k, v))
    print("\nP0  BY TTE BAND")
    for band_name, v in p0["by_tte_band"].items():
        print("   %-9s n=%-5d unhedged_frac med %-7s resid$/book_cost med %-7s"
              % (band_name, v["n_decisions"],
                 v["unhedged_fraction_abs"].get("median"),
                 v["residual_principal_over_book_cost"].get("median")))
    print("\nP2  SWITCH PREDICTION (held-out windows)")
    hdr = ("%-26s %6s %6s %7s %6s %7s %7s %8s"
           % ("model", "acc%", "prec", "recall", "F1", "balAcc", "Brier",
              "logloss"))
    print(hdr)
    for k, v in r["P2_switch_models"].items():
        o = v.get("out_of_sample")
        if not o:
            print("%-26s  %s" % (k, v.get("status")))
            continue
        print("%-26s %6s %6s %7s %6s %7s %7s %8s"
              % (k, o["accuracy_pct"], o["switch_precision"],
                 o["switch_recall"], o["switch_f1"],
                 o["balanced_accuracy"], o["brier"], o["log_loss"]))
    rl = r["run_length"]
    print("\nRUN LENGTH  n=%d  %s" % (rl["n_runs"], rl["length"]))
    print("AT THE BOUNDARY")
    for k, v in rl["at_the_boundary"].items():
        print("   %-22s %s" % (k, v))
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())

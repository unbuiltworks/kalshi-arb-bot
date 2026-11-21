"""Prospective FIDELITY runner: predict -> FREEZE -> observe -> resolve.

THE ONLY THING THAT MAKES THIS AN EXPERIMENT is that the prediction is sealed
before the action exists. The runner therefore does three things in a strict
order and never reorders them:

  1. read the observable state at time T
  2. seal a prediction for (T, T + h]
  3. ONLY AFTER h has elapsed, read what L5 actually did and append the outcome

A prediction is never amended. A model change gets a new model_version and
scores prospectively from that point; it cannot claim credit for a prediction
an earlier version made.

READ ONLY. No order path anywhere in this module or anything it imports.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.l5_fidelity import baselines as B
from src.l5_fidelity import model as M
from src.l5_fidelity import store
from src.l5_fidelity.model import HORIZONS_S, FidelityV0

MAX_H = max(HORIZONS_S)


def build_state(*, cohort: str, now: float, window_end: float,
                fills: Sequence[dict], book: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Everything observable BEFORE the prediction. `fills` are L5's fills in
    this window strictly at or before `now` -- the caller must not pass a later
    one, and the runner re-filters defensively."""
    past = [f for f in fills if f["t"] <= now]
    up = sum(f["size"] for f in past if f["outcome"] == "Up")
    dn = sum(f["size"] for f in past if f["outcome"] != "Up")
    tot = up + dn
    last = past[-1] if past else None
    return {
        "cohort": cohort,
        "t": now,
        "window_end": window_end,
        "tte_s": window_end - now,
        "n_fills_so_far": len(past),
        "since_last_fill_s": (now - last["t"]) if last else None,
        "last_outcome": (last["outcome"] if last else None),
        "last_price": (last["price"] if last else None),
        "inv_up": up, "inv_dn": dn,
        "matched_qty": min(up, dn),
        "residual_abs": abs(up - dn),
        "residual_rel": ((up - dn) / tot) if tot else None,
        "book": book,
        "book_reason": (None if book else "venue served no two-sided book"),
    }


def predict_and_freeze(out_dir: str, model: FidelityV0, state: Dict[str, Any],
                       *, now: Optional[float] = None) -> Dict[str, Any]:
    p = model.predict(tte=state["tte_s"], since=state["since_last_fill_s"],
                      resid_rel=state["residual_rel"],
                      last_outcome=state["last_outcome"],
                      price=state["last_price"])
    base = {
        "always_WAIT": B.always_wait(),
        "always_ACT": B.always_act(),
        "repeat_previous_outcome": B.repeat_previous(state["last_outcome"]),
        "DEARER": B.dearer(state["last_price"]),
        "CHEAPER": B.cheaper(state["last_price"]),
        "cohort_empirical_frequency": B.cohort_frequency(p["p_outcome_up"]),
        "historical_cadence": B.historical_rate(
            p["p_act_within"][str(min(HORIZONS_S))]),
    }
    return store.freeze(out_dir, model_version=model.version,
                        cohort=model.cohort, state=state,
                        prediction={**p, "baselines": base},
                        horizon_s=list(HORIZONS_S), now=now)


def resolve_one(out_dir: str, rec: Dict[str, Any],
                later_fills: Sequence[dict], *,
                now: Optional[float] = None) -> Dict[str, Any]:
    """Score one frozen prediction against what actually happened after it.

    `later_fills` must be L5's fills strictly AFTER the prediction time. Fills
    at or before it are dropped here as well as by the caller: a fill that
    already existed cannot be the one being predicted.
    """
    t0 = rec["prediction_utc"]
    fut = sorted((f for f in later_fills if f["t"] > t0), key=lambda f: f["t"])
    nxt = fut[0] if fut else None
    p = rec["prediction"]
    acted = {str(h): bool(nxt and (nxt["t"] - t0) <= h) for h in HORIZONS_S}
    actual = {
        "acted_within": acted,
        "next_fill_gap_s": ((nxt["t"] - t0) if nxt else None),
        "next_outcome": (nxt["outcome"] if nxt else None),
        "next_size": (nxt["size"] if nxt else None),
        "next_price": (nxt["price"] if nxt else None),
        "observed_fee_bearing": (nxt.get("fee_bearing") if nxt else None),
        "no_fill_within_max_horizon": nxt is None or (nxt["t"] - t0) > MAX_H,
    }
    dis: Dict[str, Any] = {"act": {}, "outcome": None, "size_err": None,
                           "timing_err_s": None}
    for h in HORIZONS_S:
        k = str(h)
        dis["act"][k] = {"p_pred": p["p_act_within"][k], "actual": acted[k],
                         "brier": round((p["p_act_within"][k] - (1.0 if acted[k] else 0.0)) ** 2, 6)}
    if nxt:
        dis["outcome"] = {"predicted": p["predicted_outcome"],
                          "actual": nxt["outcome"],
                          "correct": p["predicted_outcome"] == nxt["outcome"]}
        # SIZE IS SCORED AS A DISTRIBUTION, not as a point. The recorded
        # number is the probability mass the model put on the bin L5 actually
        # landed in, plus its log score; a median-minus-actual "error" on a
        # distribution running p10=2 to p99=80 measured nothing.
        szb = p.get("predicted_size_bins")
        if szb:
            actual_bin = M.size_bin(float(nxt["size"]))
            p_on = float(szb.get(actual_bin, 0.0))
            dis["size"] = {
                "actual_size": nxt["size"],
                "actual_bin": actual_bin,
                "predicted_mode_bin": p.get("predicted_size_mode_bin"),
                "mode_bin_correct": p.get("predicted_size_mode_bin") == actual_bin,
                "p_on_actual_bin": round(p_on, 6),
                "log_score": (round(math.log(p_on), 6) if p_on > 0 else None),
                "uniform_reference_p": round(1.0 / len(M.SIZE_BIN_LABELS), 6),
            }
        if p.get("predicted_size_p50_summary_only") is not None:
            dis["size_err_summary_only"] = round(
                nxt["size"] - p["predicted_size_p50_summary_only"], 6)
        if p.get("predicted_next_gap_s_p50") is not None:
            dis["timing_err_s"] = round((nxt["t"] - t0) - p["predicted_next_gap_s_p50"], 6)
    else:
        dis["outcome_reason"] = "no fill within the maximum horizon: side, size and timing are not scored"
    return store.resolve(out_dir, prediction_id=rec["prediction_id"],
                         actual=actual, disagreement=dis, now=now)

"""ESTIMAND A, fill-anchored, with the feed's latency handled honestly.

WHY THIS FILE EXISTS. Freezing predictions at wall-clock moments and resolving
them from this feed cannot measure ACT/WAIT at all. Measured 2026-09-15 over 12
samples: the newest row the activity feed serves is a MEDIAN 28.5 SECONDS OLD
(7.6-55.2s), and the age sawtooths -- the signature of batched ingestion, not
streaming. L5's BTC15m inter-fill median is 2 s. So a 30 s prediction window is
mostly invisible at resolution time, and the "ACT within 30s = 0.46" seen under
v0 is predominantly a FEED ARTIFACT rather than L5 behaviour.

THE FIX IS THE ESTIMAND AND THE ANCHOR, NOT THE SCORE. Anchor on a fill's own
timestamp and measure the gap to the next fill's own timestamp. Both come from
the same feed with the same clock, so the latency delays WHEN we can score and
not WHAT we score.

AND ONE MORE DISCIPLINE, BECAUSE SEALING IS NOT ENOUGH. A prediction sealed
before we OBSERVE an event is not prospective if the event had already OCCURRED.
The anchor is therefore the newest fill visible at freeze time, and at
resolution the successor's own timestamp is compared against the seal: a
successor that predates the seal means the outcome was already determined when
we froze, and that row is marked NOT_PROSPECTIVE and EXCLUDED from scoring
rather than counted as a hit or a miss.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

from src.l5_fidelity import baselines as B
from src.l5_fidelity import store
from src.l5_fidelity.model import ESTIMAND_A, HORIZONS_S, FidelityV0

MAX_H = max(HORIZONS_S)
# resolve only once the feed has demonstrably propagated past anchor+MAX_H.
# 55.2s was the worst observed lag; 180s is a deliberate multiple of it.
PROPAGATION_MARGIN_S = 180.0


def state_after_fill(*, cohort: str, anchor: dict,
                     window_fills: Sequence[dict]) -> Dict[str, Any]:
    """Observable state immediately AFTER the anchor fill. Only fills at or
    before the anchor's own timestamp are included."""
    past = [f for f in window_fills if f["t"] <= anchor["t"]]
    up = sum(f["size"] for f in past if f["outcome"] == "Up")
    dn = sum(f["size"] for f in past if f["outcome"] != "Up")
    tot = up + dn
    return {
        "cohort": cohort,
        "anchor_t": anchor["t"],
        "anchor_outcome": anchor["outcome"],
        "anchor_price": anchor["price"],
        "anchor_size": anchor["size"],
        "window_end": anchor["window_end"],
        "tte_s": anchor["window_end"] - anchor["t"],
        "n_fills_so_far": len(past),
        "inv_up": up, "inv_dn": dn,
        "matched_qty": min(up, dn),
        "residual_abs": abs(up - dn),
        "residual_rel": ((up - dn) / tot) if tot else None,
        "labels": ("state as of the anchor fill's own timestamp; no later fill "
                   "is included"),
    }


def freeze_anchored(out_dir: str, model: FidelityV0, state: Dict[str, Any],
                    *, now: Optional[float] = None) -> Dict[str, Any]:
    p = model.predict(tte=state["tte_s"], since=None,
                      resid_rel=state["residual_rel"],
                      last_outcome=state["anchor_outcome"],
                      price=state["anchor_price"],
                      estimand=ESTIMAND_A)
    base = {
        "always_WAIT": B.always_wait(),
        "always_ACT": B.always_act(),
        "repeat_previous_outcome": B.repeat_previous(state["anchor_outcome"]),
        "DEARER": B.dearer(state["anchor_price"]),
        "CHEAPER": B.cheaper(state["anchor_price"]),
        "cohort_empirical_frequency": B.cohort_frequency(p["p_outcome_up"]),
        "historical_cadence": B.historical_rate(
            p["p_act_within"][str(min(HORIZONS_S))]),
    }
    return store.freeze(out_dir, model_version=model.version,
                        cohort=model.cohort, state=state,
                        prediction={**p, "baselines": base},
                        horizon_s=list(HORIZONS_S), now=now)


def resolve_anchored(out_dir: str, rec: Dict[str, Any],
                     window_fills: Sequence[dict], *,
                     now: Optional[float] = None) -> Dict[str, Any]:
    """Score one anchored prediction. Gaps are measured between the fills' OWN
    timestamps, so feed latency cannot distort them."""
    anchor_t = rec["state"]["anchor_t"]
    seal_t = rec["prediction_utc"]
    fut = sorted((f for f in window_fills if f["t"] > anchor_t),
                 key=lambda f: f["t"])
    nxt = fut[0] if fut else None
    p = rec["prediction"]

    # THE PROSPECTIVITY CHECK. A successor that already existed when we sealed
    # was never predicted, whatever the seal says.
    prospective = True
    reason = None
    if nxt is not None and nxt["t"] <= seal_t:
        prospective = False
        reason = (f"successor at {nxt['t']} predates the seal at {seal_t:.0f}: "
                  "the outcome was already determined when the prediction was "
                  "frozen, so this row is EXCLUDED from scoring")

    acted = {str(h): bool(nxt and (nxt["t"] - anchor_t) <= h) for h in HORIZONS_S}
    actual = {
        "prospective": prospective,
        "not_prospective_reason": reason,
        "acted_within": acted,
        "next_fill_gap_s": ((nxt["t"] - anchor_t) if nxt else None),
        "next_outcome": (nxt["outcome"] if nxt else None),
        "next_size": (nxt["size"] if nxt else None),
        "next_price": (nxt["price"] if nxt else None),
        "observed_fee_bearing": (nxt.get("fee_bearing") if nxt else None),
        "no_fill_within_max_horizon": nxt is None or (nxt["t"] - anchor_t) > MAX_H,
    }
    dis: Dict[str, Any] = {"estimand": ESTIMAND_A, "act": {}, "outcome": None,
                           "size_err": None, "timing_err_s": None,
                           "scored": prospective}
    for h in HORIZONS_S:
        k = str(h)
        pv = p["p_act_within"][k]
        dis["act"][k] = {"p_pred": pv, "actual": acted[k],
                         "brier": (round((pv - (1.0 if acted[k] else 0.0)) ** 2, 6)
                                   if pv is not None else None)}
    if nxt and prospective:
        dis["outcome"] = {"predicted": p["predicted_outcome"],
                          "actual": nxt["outcome"],
                          "correct": p["predicted_outcome"] == nxt["outcome"]}
        if p.get("predicted_size_p50") is not None:
            dis["size_err"] = round(nxt["size"] - p["predicted_size_p50"], 6)
        if p.get("predicted_next_gap_s_p50") is not None:
            dis["timing_err_s"] = round((nxt["t"] - anchor_t)
                                        - p["predicted_next_gap_s_p50"], 6)
    elif not prospective:
        dis["outcome_reason"] = reason
    else:
        dis["outcome_reason"] = "no fill within the maximum horizon"
    return store.resolve(out_dir, prediction_id=rec["prediction_id"],
                         actual=actual, disagreement=dis, now=now)

"""L5_CONTINUOUS_INVENTORY_V1 — SHADOW controller.

SHADOW IS STRUCTURAL HERE, NOT A FLAG. This module imports no transport, holds
no credentials, and contains no order verb. The service that runs it is
deployed with no API key at all, so "shadow only" is a property of what exists
rather than a switch someone could flip.

WHAT IT DOES. At every observable market state it runs the FIDELITY decision
FIRST -- L5's predicted side, price/aggressiveness, raw desired quantity and
desired inventory, in L5's own share units -- and only then applies the ADAPTED
risk scale k. Both layers are recorded on every row, so a risk-driven departure
can never be mistaken for a model prediction.

    state -> FIDELITY(side, price, raw_desired_qty, desired_inventory)
          -> k
          -> ADAPTED(quantized_desired_qty, scaled_inventory, proposed_action)

THE SUBTLETY THAT DECIDES WHETHER ANY OF THIS TRANSFERS, and it is not sizing.
The model's state key buckets residual inventory in L5's SHARE units: <1 share
is "flat", 1-10 is "imb1-10", and so on. Our inventory is k times smaller. Look
the state up with our raw contract residual and a scaled-down clone reads "flat"
essentially forever -- it would ask the model what L5 does from a FLAT book
while actually holding inventory, and get OPEN back every time. That is the R1
failure one level deeper than sizing, and no amount of correct multiplication
fixes it.

So the residual is lifted back into L5 units before the lookup:

    residual_l5_equivalent = our_residual / k

`state_key_naive` is computed alongside it on purpose, so the shadow can
measure how often the two disagree rather than asserting the correction was
needed.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.polymarket_l5 import action_model as am

LABEL = ("SHADOW ONLY -- no transport, no credentials, no order verb. "
         "Every proposed action is observational. Nothing here is submitted, "
         "priced into a live book, or counted as P&L.")

SCHEMA = "l5_v1_shadow/observation/v1"

# Envelope A, owner-approved 2026-09-16 for SHADOW. The scale is DERIVED from
# these; it is never edited directly, and this authorization is not a licence
# for $30 of live exposure.
ENVELOPE_A = {
    "residual_ceiling_usd": 3.00,
    "exposure_ceiling_usd": 30.00,
    "daily_realized_loss_stop_usd": 6.00,
    "max_windows_per_day": 2,
}
RISK_SCALE_K = 0.00619
SHAPE_FLOOR_K = 0.006          # below this the trajectory stops being L5's

VENUE_MIN_QTY = 0.01
VENUE_QTY_STEP = 0.01

# Divergence is attributed to exactly one cause, and the three are disjoint by
# construction: the model is asked first, the envelope second, the venue last.
DIV_NONE = "NONE"
DIV_MODEL = "MODEL_DISAGREEMENT"
DIV_RISK = "RISK_CAP_FORCED"
DIV_VENUE = "VENUE_QUANTIZATION_FORCED"

# A quantization that moves the requested size by more than this is recorded as
# venue-forced rather than waved through as rounding.
QUANT_TOLERANCE = 0.25


def quantize(q: float, min_qty: float = VENUE_MIN_QTY,
             step: float = VENUE_QTY_STEP,
             max_leg: Optional[float] = None) -> float:
    if q <= 0:
        return 0.0
    out = max(min_qty, round(q / step) * step)
    if max_leg is not None:
        out = min(out, max_leg)
    return round(out, 10)


# --------------------------------------------------------------------------
# FIDELITY: what the model says L5 does here, in L5's own units

def fidelity_decision(model: Mapping[str, Any],
                      fidelity_art: Optional[Mapping[str, Any]],
                      *, last_price: float, tte_s: float,
                      residual_l5_units: float,
                      inventory_l5_units: float,
                      last_rel: str = "open",
                      last_size: str = "q<5") -> dict:
    """L5's predicted next action from this state. No risk scaling applied.

    Uses the DEPLOYED v2 artifact through its own loader rather than a second
    reading of the same JSON: `pmus_l5_persistence_v2.v2_dist` applies the
    full -> base -> marginal shrinkage that every other consumer of this model
    already uses. A shadow that scored against a differently-read model would
    be measuring my parsing, not L5.
    """
    from src import pmus_l5_fidelity_live as fid
    from src import pmus_l5_persistence_v2 as pv2

    state = am.state_key(last_price, tte_s, residual_l5_units)
    try:
        d = pv2.v2_dist(model, state, last_rel, last_size)
    except (KeyError, TypeError, ZeroDivisionError) as exc:
        return {"state": state, "observed_by_l5": False, "n": 0,
                "relation": None, "relation_probs": {}, "modal_action": None,
                "raw_desired_qty": None, "desired_inventory_l5": None,
                "p_taker": None,
                "reason": f"model_unavailable:{type(exc).__name__}"}

    dist = d.get("dist") or {}
    if not dist:
        return {"state": state, "observed_by_l5": False, "n": 0,
                "relation": None, "relation_probs": {}, "modal_action": None,
                "raw_desired_qty": None, "desired_inventory_l5": None,
                "p_taker": None, "reason": "state_never_observed_by_l5"}

    probs = pv2.relation_mass(dist)
    modal = max(dist, key=lambda a: dist[a])
    relation = str(modal).split("|")[0]
    stats = {"n": d.get("n"), "backed_off": d.get("backed_off"),
             "lookup_state": d.get("state")}

    size = fid.l5_target_size(fidelity_art, state, relation or "")
    raw_qty = size.get("shares_p50")
    agg = fid.l5_target_aggressiveness(fidelity_art, tte_s, relation or "")

    # side: OPEN/EXTEND add to the surplus side, COMPLETE reduces it
    if relation == "complete":
        sign = -1.0 if inventory_l5_units >= 0 else 1.0
    else:
        sign = 1.0 if inventory_l5_units >= 0 else -1.0

    desired_inv = (inventory_l5_units + sign * float(raw_qty)
                   if raw_qty is not None else None)
    return {
        "state": state,
        # "observed" means L5 was actually seen in THIS state, not that the
        # shrinkage found a prior to fall back on. A backed-off row is a real
        # prediction and is scored, but it must not be counted as coverage.
        "observed_by_l5": not stats["backed_off"],
        "n": stats.get("n"), "backed_off": stats["backed_off"],
        "lookup_state": stats["lookup_state"],
        "relation": relation, "relation_probs": probs, "modal_action": modal,
        "raw_desired_qty": raw_qty, "desired_inventory_l5": desired_inv,
        "side_sign": sign,
        "size_source": size.get("source"), "size_n": size.get("n"),
        "p_taker": agg.get("p_taker"), "tte_bucket": agg.get("tte_bucket"),
        "live_policy": "maker/post-only only; taker disabled",
        "reason": None,
    }


# --------------------------------------------------------------------------
# ADAPTED: the same decision at our scale

def adapted_action(fidelity: Mapping[str, Any], *, k: float,
                   our_inventory: float, book: Mapping[str, Any],
                   envelope: Mapping[str, Any] = ENVELOPE_A,
                   spent_usd: float = 0.0,
                   min_qty: float = VENUE_MIN_QTY) -> dict:
    """Scale L5's desired quantity, then let the envelope and the venue speak.

    Order matters and is the whole point: the model is asked first, so a size
    the envelope refuses is recorded as RISK_CAP_FORCED rather than as
    something the model wanted.
    """
    raw = fidelity.get("raw_desired_qty")
    if raw is None:
        return {"proposed": None, "divergence": DIV_MODEL,
                "divergence_reason": fidelity.get("reason")
                or "model returned no desired size",
                "scaled_desired_qty": None, "quantized_qty": None,
                "scaled_desired_inventory": None}

    scaled = k * float(raw)
    desired_inv_scaled = (k * float(fidelity["desired_inventory_l5"])
                          if fidelity.get("desired_inventory_l5") is not None
                          else None)

    price = book.get("touch")
    px = float(price) if price is not None else None

    # --- envelope, priced in dollars
    resid_ceiling = float(envelope["residual_ceiling_usd"])
    expo_ceiling = float(envelope["exposure_ceiling_usd"])
    allowed = scaled
    risk_binding = None
    if px is not None and px > 0:
        room_expo = max(0.0, (expo_ceiling - spent_usd) / px)
        if room_expo < allowed:
            allowed, risk_binding = room_expo, "exposure_ceiling"
        projected_resid = abs(our_inventory + fidelity["side_sign"] * allowed)
        room_resid = max(0.0, resid_ceiling / px)
        if projected_resid > room_resid:
            trimmed = max(0.0, room_resid - abs(our_inventory))
            if trimmed < allowed:
                allowed, risk_binding = trimmed, "residual_ceiling"

    # --- venue
    q = quantize(allowed, min_qty=min_qty)
    if allowed <= 0:
        q = 0.0

    div, reason = DIV_NONE, None
    if risk_binding is not None:
        div, reason = DIV_RISK, risk_binding
    elif q > 0 and abs(q - scaled) > QUANT_TOLERANCE * max(scaled, 1e-12):
        div = DIV_VENUE
        reason = ("pinned_to_venue_minimum" if q <= min_qty
                  else "rounded_to_venue_step")
    return {
        "k": k,
        "scaled_desired_qty": scaled,
        "risk_allowed_qty": allowed,
        "quantized_qty": q,
        "scaled_desired_inventory": desired_inv_scaled,
        "proposed": ({"relation": fidelity["relation"],
                      "side_sign": fidelity["side_sign"],
                      "qty": q, "price": px, "post_only": True}
                     if q > 0 else None),
        "divergence": div, "divergence_reason": reason,
    }


# --------------------------------------------------------------------------
# hypothetical economics from the ACTUAL observed book

def executable_economics(book: Mapping[str, Any], relation: Optional[str],
                         qty: Optional[float],
                         held_avg_cost: Optional[float]) -> dict:
    """What this action would have cost against the book we actually saw.

    THEORETICAL. No order is placed and no fill is assumed: a maker quote is
    reported as feasible or not, never as filled."""
    bid, ask = book.get("bid"), book.get("ask")
    if bid is None or ask is None:
        return {"available": False, "why": "book_not_two_sided"}
    b, a = float(bid), float(ask)
    out = {
        "available": True, "bid": b, "ask": a,
        "maker_feasible": bool(b < a),
        "maker_price": b,
        "crosses_if_posted_at_touch": bool(b >= a),
        "labels": "THEORETICAL; no order placed, no fill assumed",
    }
    if held_avg_cost is not None and relation == "complete":
        complement = 1.0 - b
        out["pair_cost_at_touch"] = round(float(held_avg_cost) + complement, 6)
    if qty:
        out["notional_at_touch"] = round(b * float(qty), 8)
    return out


# --------------------------------------------------------------------------
# one observation

def observe(*, model: Mapping[str, Any],
            fidelity_art: Optional[Mapping[str, Any]],
            slug: str, now: float, book: Mapping[str, Any],
            our_inventory: float, spent_usd: float = 0.0,
            tte_s: float = 1e9, k: float = RISK_SCALE_K,
            envelope: Mapping[str, Any] = ENVELOPE_A,
            held_avg_cost: Optional[float] = None) -> dict:
    """One state -> one row. A WAIT is a row too, with its reason."""
    bid = book.get("bid")
    last_price = float(bid) if bid is not None else 0.0

    # THE LIFT. Our inventory is k times L5's; the model's state key buckets in
    # L5 share units, so it has to be asked in those units or it is answering a
    # different question. `naive` is kept to measure how often that matters.
    resid_l5 = (our_inventory / k) if k else 0.0
    state_faithful = am.state_key(last_price, tte_s, resid_l5)
    state_naive = am.state_key(last_price, tte_s, our_inventory)

    fid = fidelity_decision(model, fidelity_art, last_price=last_price,
                            tte_s=tte_s, residual_l5_units=resid_l5,
                            inventory_l5_units=resid_l5)
    touch = {"touch": bid, **dict(book)}
    adp = adapted_action(fid, k=k, our_inventory=our_inventory, book=touch,
                         envelope=envelope, spent_usd=spent_usd)
    econ = executable_economics(
        book, fid.get("relation"),
        (adp.get("proposed") or {}).get("qty"), held_avg_cost)

    acted = adp.get("proposed") is not None
    return {
        "schema": SCHEMA, "slug": slug, "t": now, "labels": LABEL,
        "k": k, "envelope": dict(envelope),
        "state_l5_units": state_faithful,
        "state_naive_our_units": state_naive,
        "state_key_lift_changed_it": state_faithful != state_naive,
        "residual_l5_equivalent": resid_l5,
        "our_inventory": our_inventory,
        "fidelity": fid,
        "adapted": adp,
        "economics": econ,
        "action": "PROPOSE" if acted else "WAIT",
        "wait_reason": (None if acted else
                        (adp.get("divergence_reason")
                         or fid.get("reason") or "no_size_after_scaling")),
    }


# --------------------------------------------------------------------------
# aggregation

def summarize(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Every measure the owner asked to see, kept separate.

    WAIT rows are included everywhere on purpose: scoring only the states where
    the clone trades is how a policy that almost never trades scores perfectly.
    """
    if not rows:
        return {"n": 0}
    n = len(rows)
    proposed = [r for r in rows if r["action"] == "PROPOSE"]
    waits = [r for r in rows if r["action"] == "WAIT"]
    observed = [r for r in rows if r["fidelity"].get("observed_by_l5")]

    div = {}
    for r in rows:
        d = r["adapted"].get("divergence") or DIV_NONE
        div[d] = div.get(d, 0) + 1

    lift = sum(1 for r in rows if r["state_key_lift_changed_it"])

    raw = [r["fidelity"]["raw_desired_qty"] for r in proposed
           if r["fidelity"].get("raw_desired_qty") is not None]
    ours = [r["adapted"]["quantized_qty"] for r in proposed
            if r["fidelity"].get("raw_desired_qty") is not None]
    from analysis.l5.live_v1.risk_scale import _spearman
    rank = _spearman(raw, ours) if len(raw) >= 3 else None

    pinned = sum(1 for q in ours if q <= VENUE_MIN_QTY)
    return {
        "n": n,
        "proposed": len(proposed), "wait": len(waits),
        "wait_share": round(len(waits) / n, 4),
        "states_observed_by_l5": len(observed),
        "state_coverage": round(len(observed) / n, 4),
        "divergence_attribution": div,
        "state_key_lift_changed_it": lift,
        "state_key_lift_share": round(lift / n, 4),
        "relative_size_rank_vs_l5": rank,
        "pinned_to_venue_minimum": pinned,
        "pinned_share": (round(pinned / len(ours), 4) if ours else None),
        "relations": _count(r["fidelity"].get("relation") for r in rows),
        "wait_reasons": _count(r.get("wait_reason") for r in waits),
        "maker_feasible": sum(1 for r in rows
                              if (r["economics"] or {}).get("maker_feasible")),
        "labels": LABEL,
    }


def _count(xs) -> dict:
    out: Dict[str, int] = {}
    for x in xs:
        key = str(x)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))

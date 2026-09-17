"""L5_PASSIVE_SURFACE_ENGINE_V1 -- emit a desired SURFACE, not a next trade.

WHAT CHANGES FROM THE ACTION ENGINE. `DesiredInventoryAction` answers "what
should we do next". That framing already assumes the trader is choosing a
trade. The working hypothesis is that L5 does not choose trades at all: he
maintains a standing set of passive levels, and what looks like a decision
sequence is the market arriving at that surface in whatever order it arrives.

So the output here is a set of levels with persistence instructions:

    market state
      -> desired quote levels per outcome
      -> parent / base quantities
      -> persistence & refresh instructions
      -> inventory / risk overlay

and only THEN, for the existing venue-neutral spine, a decomposition into one
`DesiredInventoryAction` per rung so `l5_router.route` can answer per venue.
The surface is the primitive; the actions are a projection of it.

NOTHING HERE IS A FILL, AN ORDER OR A PRICE TO SUBMIT. A rung carries an
economic ceiling, exactly as an intent does; whether a venue can rest there is
the adapter's question. This module has no client, no signer and no submit
path, and a test asserts it.

IT REFUSES RATHER THAN DEFAULTS. Every load-bearing parameter is consulted
through `SurfaceParams.get`, which raises on UNRESOLVED. A set with unknowns
therefore yields `emitted: False` and the list of fields that blocked it --
never a surface built on numbers nobody measured.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.l5_intent.action import DesiredInventoryAction
from src.l5_surface import params as PR

SCHEMA = "l5_desired_passive_surface/v1"

#: which parameters the geometry cannot be computed without. Declared, so a
#: refusal names the same fields every time rather than whichever one happened
#: to be consulted first.
LOAD_BEARING: Tuple[str, ...] = (
    "rung_count", "tick_offsets", "base_parent_qty", "secondary_rung_qty",
    "both_sides_policy", "inventory_price_skew", "inventory_size_skew",
    "refresh_policy", "replenishment_policy", "minimum_rest_time",
    "withdrawal_policy", "TTE_policy", "max_residual",
)


@dataclass(frozen=True)
class Rung:
    outcome: str
    side: str                 # "BUY" -- a passive surface here is bids
    rung_index: int
    ticks_behind_ask: int
    max_acceptable_price: str
    desired_qty: str
    role: str                 # "BASE" or "SECONDARY"
    suppressed_by: Optional[str] = None
    attribution: str = "INFERRED_SURFACE"


@dataclass(frozen=True)
class DesiredSurface:
    cohort: str
    market_key: str
    state_id: str
    t_utc: float
    tte_s: float
    spec_label: str
    spec_sha256: str
    rungs: List[Rung]
    persistence: Dict[str, Any]
    overlay: Dict[str, Any]
    attribution: str = "INFERRED_SURFACE"

    def surface_id(self) -> str:
        blob = json.dumps({"k": self.market_key, "s": self.state_id,
                           "spec": self.spec_sha256,
                           "r": [asdict(r) for r in self.rungs]},
                          sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def live_rungs(self) -> List[Rung]:
        return [r for r in self.rungs if r.suppressed_by is None]

    def report(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "surface_id": self.surface_id(),
                "cohort": self.cohort, "market_key": self.market_key,
                "state_id": self.state_id, "t_utc": self.t_utc,
                "tte_s": self.tte_s, "spec_label": self.spec_label,
                "spec_sha256": self.spec_sha256,
                "rungs": [asdict(r) for r in self.rungs],
                "n_live_rungs": len(self.live_rungs()),
                "persistence": self.persistence, "overlay": self.overlay,
                "attribution": "INFERRED_SURFACE",
                "labels": [
                    "INFERRED_SURFACE. This is OUR hypothesis about what "
                    "should be resting, never an observation of L5's orders.",
                    "max_acceptable_price is an economic ceiling. No "
                    "submission price is decided here and no fill is implied.",
                ]}


def _tick(o: Mapping[str, Any]) -> D:
    t = o.get("tick")
    return D(str(t)) if t is not None else D("0.01")


def _residual(inv: Mapping[str, Any]) -> D:
    return D(str(inv.get("Up", 0) or 0)) - D(str(inv.get("Down", 0) or 0))


def desired_surface(state: Mapping[str, Any], sp: PR.SurfaceParams
                    ) -> Dict[str, Any]:
    """The surface this state calls for, or an explicit refusal.

    A refusal is a first-class result. `blocked_by` lists every load-bearing
    field that is UNRESOLVED, so the answer to "why is there no surface" is a
    list of measurements nobody has made rather than a silence.
    """
    blocked = [f for f in LOAD_BEARING if f in sp.unresolved()]
    if blocked:
        return {
            "emitted": False, "blocked_by": blocked,
            "spec_label": sp.label, "spec_sha256": sp.spec_sha256(),
            "market_key": state.get("market_key"),
            "reason": ("these parameters are UNRESOLVED and no default is "
                       "substituted for them"),
            "labels": [
                "NOT a failure. A surface emitted here would be built from "
                "numbers chosen rather than measured, and would then be cited "
                "as what L5 does.",
                "Populate them ONLY from L5_PASSIVE_SURFACE_PROSPECTIVE_V1, "
                "field by field, and only where it establishes the value.",
            ],
        }

    outs: Mapping[str, Any] = state.get("outcomes") or {}
    inv = state.get("inventory") or {}
    tte = float(state.get("tte_s") or 0.0)
    res = _residual(inv)

    tte_pol = sp.get("TTE_policy")
    quote_window = tte_pol.get("quote_between_tte_s")
    if quote_window and not (float(quote_window[0]) <= tte
                             <= float(quote_window[1])):
        return {"emitted": False, "blocked_by": [],
                "spec_label": sp.label, "spec_sha256": sp.spec_sha256(),
                "market_key": state.get("market_key"),
                "reason": "TTE_policy withholds the surface at tte_s=%.1f" % tte,
                "tte_s": tte}

    sides_pol = sp.get("both_sides_policy")
    scope = list(sides_pol.get("outcomes") or outs.keys())
    offsets = list(sp.get("tick_offsets"))
    n_rungs = int(sp.get("rung_count"))
    base_q = D(str(sp.get("base_parent_qty")))
    sec_q = D(str(sp.get("secondary_rung_qty")))
    px_skew = sp.get("inventory_price_skew")
    sz_skew = sp.get("inventory_size_skew")
    max_res = D(str(sp.get("max_residual")))

    rungs: List[Rung] = []
    for outcome in scope:
        o = outs.get(outcome)
        if not o or o.get("ask") is None:
            continue
        tick = _tick(o)
        ask = D(str(o["ask"]))
        # which direction does holding MORE of this outcome push the residual?
        widening = (res > 0 and outcome == "Up") or (res < 0
                                                     and outcome == "Down")
        for i in range(n_rungs):
            if i >= len(offsets):
                break
            off = int(offsets[i])
            # the price skew shifts a side's levels by whole ticks when
            # inventory leans; it is a SHIFT of our own hypothesis, not an
            # observation, and 0 means "no skew supported"
            shift = int((px_skew or {}).get(
                "widening_ticks" if widening else "reducing_ticks", 0) or 0)
            px = ask - (off + shift) * tick
            if px <= 0 or px >= 1:
                continue
            q = base_q if i == 0 else sec_q
            mult = D(str((sz_skew or {}).get(
                "widening_multiplier" if widening
                else "reducing_multiplier", 1) or 1))
            q = q * mult
            sup = None
            if widening and abs(res) >= max_res:
                sup = ("max_residual: |residual| %s >= %s and this rung would "
                       "widen it" % (abs(res), max_res))
            rungs.append(Rung(
                outcome=outcome, side="BUY", rung_index=i,
                ticks_behind_ask=off + shift,
                max_acceptable_price=str(px),
                desired_qty=str(q),
                role=("BASE" if i == 0 else "SECONDARY"),
                suppressed_by=sup))

    surf = DesiredSurface(
        cohort=str(state.get("cohort") or ""),
        market_key=str(state.get("market_key") or ""),
        state_id=str(state.get("state_id") or ""),
        t_utc=float(state.get("t_utc") or 0.0),
        tte_s=tte,
        spec_label=sp.label, spec_sha256=sp.spec_sha256(),
        rungs=rungs,
        persistence={
            "refresh_policy": sp.get("refresh_policy"),
            "replenishment_policy": sp.get("replenishment_policy"),
            "minimum_rest_time_s": sp.get("minimum_rest_time"),
            "withdrawal_policy": sp.get("withdrawal_policy"),
            "labels": ("instructions carried WITH the surface. Nothing here "
                       "cancels or places anything; an adapter reads them."),
        },
        overlay={
            "residual": str(res),
            "max_residual": str(max_res),
            "inventory": {k: str(v) for k, v in inv.items()},
            "suppressed_rungs": sum(1 for r in rungs
                                    if r.suppressed_by is not None),
        },
    )
    return {"emitted": True, "surface": surf, "report": surf.report()}


def to_intents(surf: DesiredSurface, *, model_version: str,
               inventory: Mapping[str, Any]) -> List[DesiredInventoryAction]:
    """Project the surface onto the existing venue-neutral spine.

    One intent per LIVE rung, so `l5_router.route` can answer per venue
    exactly as it does for the action engine. A suppressed rung produces no
    intent: the risk overlay must not be re-decidable downstream.

    The action label is derived from inventory, not chosen: acquiring the side
    we are already long is an EXTEND, the other side a COMPLETE when a
    residual exists, and an OPEN when we hold nothing.
    """
    up = D(str(inventory.get("Up", 0) or 0))
    dn = D(str(inventory.get("Down", 0) or 0))
    res = up - dn
    matched = min(up, dn)
    out: List[DesiredInventoryAction] = []
    for r in surf.live_rungs():
        held = up if r.outcome == "Up" else dn
        if up == 0 and dn == 0:
            act = "OPEN"
        elif (res > 0 and r.outcome == "Up") or (res < 0
                                                 and r.outcome == "Down"):
            act = "EXTEND"
        elif res != 0:
            act = "COMPLETE"
        else:
            act = "EXTEND"
        out.append(DesiredInventoryAction(
            cohort=surf.cohort, market_key=surf.market_key,
            source_state_id=surf.state_id, t_utc=surf.t_utc, tte_s=surf.tte_s,
            action=act, desired_outcome=r.outcome,
            desired_inventory=str(held + D(r.desired_qty)),
            incremental_qty=r.desired_qty,
            max_acceptable_price=r.max_acceptable_price,
            current_matched=str(matched), residual=str(res),
            model_version=model_version,
            notes=("surface rung %d of %s, %d ticks behind ask; "
                   "INFERRED_SURFACE" % (r.rung_index, surf.spec_label,
                                         r.ticks_behind_ask)),
            extra={"surface_id": surf.surface_id(),
                   "spec_sha256": surf.spec_sha256,
                   "rung_index": r.rung_index, "rung_role": r.role,
                   "attribution": "INFERRED_SURFACE"},
        ))
    return out

"""Behavioral residual saturation control -- SHADOW ARMS, no order path.

THE HYPOTHESIS, from the corrected replay. Temporary residual is useful up to
roughly 20-50 L5 shares: repair probability rises 46% -> 94% across that range
and then saturates at ~95%, while the attained effective pair is FLAT at ~0.972
in every bucket. Past saturation, more inventory buys tail risk and nothing
else -- above 500 shares the repair rate collapses to 37.5%.

WHAT A CAP MEANS, AND THE FOUR THINGS IT DOES NOT MEAN. This distinction is the
whole design and every part of it is pinned by a test:

    A cap STOPS INCREASING directional residual once reached.

    It does NOT liquidate the residual.
    It does NOT refuse an economically useful complement fill.
    It does NOT wait for an arbitrary cheap-pair threshold.
    It does NOT force completion at expiry.

Once capped the engine may keep doing anything that REDUCES residual or grows
matched inventory, subject to the existing executable economics and the
unchanged safety envelope. A cap is a brake on one direction, not a mode.

THE UNIT PROBLEM, AND WHY THIS GATES ON SHARES. The replay's cap is in L5
SHARES. Production's existing `residual_cap_units` gates on
`behavioral_residual()`, which is UNITS -- contracts divided by the executable
contracts in one behavioral unit. The two are related by L5's own p50 fill size
for the state and relation, which is STATE-DEPENDENT and not a constant. So
setting residual_cap_units = 20/35/50 would not express the replay result; it
would express a number ~10x larger and varying by state.

This module therefore gates on `behavioral_residual_shares()`, which the engine
already maintains in exactly the replay's unit, and converts only for reporting.
For the record, at the observed shares-per-unit of 10.0 the production cap of
6 units is 60 L5 shares -- ABOVE the saturation band, so every arm here is
tighter than what production runs today and none increases exposure.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Optional

SCHEMA = "l5_residual_saturation/v1"
ARMS = ("CONTROL", "cap20", "cap35", "cap50")
CAP_SHARES: Dict[str, Optional[float]] = {
    "CONTROL": None, "cap20": 20.0, "cap35": 35.0, "cap50": 50.0}

R_NO_SHARES = ("behavioral residual in shares unavailable: no fill size "
               "credited yet, so the cap cannot be evaluated in the replay's "
               "unit")


def shares_per_unit(residual_units: Optional[float],
                    residual_shares: Optional[float]) -> Optional[float]:
    """The live conversion factor, derived rather than assumed.

    Returns None when it cannot be established -- never a default of 10.0,
    because a wrong factor silently rescales the cap by an order of magnitude.
    """
    if not residual_units or residual_shares is None:
        return None
    if abs(residual_units) < 1e-12:
        return None
    return residual_shares / residual_units


def units_for(cap_shares: float, spu: Optional[float]) -> Optional[float]:
    """cap in L5 shares -> cap in production behavioral units. None when the
    factor is unknown; the caller must then fail closed, not guess."""
    if spu is None or spu == 0:
        return None
    return cap_shares / spu


def contracts_for(cap_units: Optional[float], unit_contracts: Any) -> Optional[D]:
    """...and units -> executable contracts, the third leg of the chain
    replay shares -> behavioral units -> venue contracts/dollars."""
    if cap_units is None or unit_contracts in (None, 0):
        return None
    return D(str(cap_units)) * D(str(unit_contracts))


def would_increase_residual(*, residual_shares: float, side_up: bool,
                            add_shares: float) -> bool:
    """Does this action push |residual| further from zero?"""
    new = residual_shares + (add_shares if side_up else -add_shares)
    return abs(new) > abs(residual_shares)


def decide(*, arm: str, residual_shares: Optional[float], side_up: bool,
           add_shares: float, relation: Optional[str] = None) -> Dict[str, Any]:
    """One arm's verdict on one proposed action. OBSERVATION ONLY.

    Returns `allow` plus the reason, always -- a refusal without a reason is
    indistinguishable from an absence of evidence.
    """
    cap = CAP_SHARES.get(arm)
    out: Dict[str, Any] = {
        "schema": SCHEMA, "arm": arm, "cap_shares": cap,
        "residual_shares": residual_shares, "relation": relation,
        "labels": ("SHADOW ONLY -- no order path. A cap stops INCREASING "
                   "residual; it never liquidates, never refuses a useful "
                   "complement, never waits for a cheap-pair threshold and "
                   "never forces completion at expiry."),
    }
    if cap is None:
        return {**out, "allow": True, "reason": "CONTROL: exact current behaviour"}
    if residual_shares is None:
        # fail closed on the DIRECTION THAT ADDS RISK only: an unknown residual
        # must not authorise more of it, but must never block a repair either.
        return {**out, "allow": False, "reason": R_NO_SHARES,
                "fail_closed": True}
    increases = would_increase_residual(residual_shares=residual_shares,
                                        side_up=side_up, add_shares=add_shares)
    if not increases:
        return {**out, "allow": True,
                "reason": "reduces or holds residual: never blocked by a cap"}
    if abs(residual_shares) >= cap:
        return {**out, "allow": False,
                "reason": f"saturated: |residual| {abs(residual_shares):.4f} "
                          f">= cap {cap} L5 shares; stop ADDING only"}
    return {**out, "allow": True,
            "reason": f"|residual| {abs(residual_shares):.4f} < cap {cap}"}


def conversion_report(*, residual_units: Optional[float],
                      residual_shares: Optional[float],
                      unit_contracts: Any) -> Dict[str, Any]:
    """The full documented chain, emitted so it is auditable per decision
    rather than asserted once in a document."""
    spu = shares_per_unit(residual_units, residual_shares)
    rows = {}
    for arm, cap in CAP_SHARES.items():
        if cap is None:
            rows[arm] = {"cap_shares": None, "cap_units": None,
                         "cap_contracts": None, "reason": "CONTROL"}
            continue
        u = units_for(cap, spu)
        c = contracts_for(u, unit_contracts)
        rows[arm] = {
            "cap_shares": cap,
            "cap_units": u,
            "cap_contracts": (str(c) if c is not None else None),
            "reason": (None if u is not None else
                       "shares-per-unit not establishable from this state"),
        }
    return {
        "schema": SCHEMA,
        "shares_per_unit": spu,
        "shares_per_unit_reason": (None if spu is not None else
                                   "needs a non-zero residual in both units"),
        "unit_contracts": (str(unit_contracts) if unit_contracts is not None else None),
        "arms": rows,
        "production_cap_units_today": 6,
        "production_cap_in_shares": (6 * spu if spu is not None else None),
        "labels": ("replay shares -> behavioral units -> venue contracts. The "
                   "factor is DERIVED per state, never defaulted: a wrong one "
                   "rescales the cap by an order of magnitude."),
    }

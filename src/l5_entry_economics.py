"""Conservative, side-effect-free first-leg economics. NOT a fill predictor.

All cash amounts are NET per contract (fees/rebates included if confirmed).
The completion probability must be conditional on an actual first-leg fill,
not the unconditional frequency of opening orders filling. No default or
historical-book-touch approximation is allowed. No production wiring.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional


def _decimal(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def evaluate_opening(
    *,
    completed_net_per_contract: Any,
    residual_net_per_contract: Any,
    completion_probability_given_first_fill: Any,
    minimum_net_edge_per_contract: Any = "0",
) -> Dict[str, Any]:
    """Return conditional net expectation or UNKNOWN, never a fabricated fill.

    The completed and residual outcomes must use the same unit and include
    all relevant execution costs. The supplied probability must come from
    an independently validated, comparable cohort; this function cannot
    attest that provenance. A scenario is NOT a live admission signal.
    """
    gain = _decimal(completed_net_per_contract)
    residual = _decimal(residual_net_per_contract)
    p = _decimal(completion_probability_given_first_fill)
    floor = _decimal(minimum_net_edge_per_contract)
    if any(x is None for x in (gain, residual, p, floor)):
        return {"status": "UNKNOWN", "reason": "MISSING_OR_INVALID_INPUT",
                "admissible": False}
    if p < 0 or p > 1:
        return {"status": "UNKNOWN", "reason": "INVALID_PROBABILITY",
                "admissible": False}
    if gain <= residual:
        return {"status": "UNKNOWN", "reason": "NON_IMPROVING_COMPLETION",
                "admissible": False}
    expected = p * gain + (Decimal(1) - p) * residual
    threshold = (floor - residual) / (gain - residual)
    return {
        "status": "SCENARIO_ONLY",
        "admissible": False,  # never silently authorize real openings
        "scenario_clears_edge": expected > floor,
        "conditional_expected_net_per_contract": str(expected),
        "break_even_completion_probability": str(threshold),
        "completion_probability_given_first_fill": str(p),
        "minimum_net_edge_per_contract": str(floor),
        "reason": "UNATTESTED_PROBABILITY_AND_NET_OUTCOMES",
    }

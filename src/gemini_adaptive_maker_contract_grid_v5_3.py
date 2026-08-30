"""Instrument-specific quantity-grid adapter for Gemini adaptive maker V5.3.

Gemini prediction-market contracts publish quantityMinimum and quantityIncrement per
instrument. V5.3 deliberately trades only integer clips q=1..10, but it must not
assume quantityIncrement == 1. This adapter computes the integer clips that are
actually valid on the live contract grid and patches the V5.3 planner to consider
only that set.

No network calls and no order entry.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any

from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_policy_v5_3 as policy

ZERO = D("0")
MAX_Q = 10
_ORIGINAL_SELECT_PLAN = policy.select_plan
_current_valid_quantities: tuple[int, ...] | None = None
_current_metadata: tuple[D, D, D] | None = None


def _is_increment_multiple(quantity: D, increment: D) -> bool:
    if increment <= ZERO:
        return False
    ratio = quantity / increment
    return ratio == ratio.to_integral_value()


def valid_integer_quantities(event: Any) -> tuple[int, ...]:
    """Return valid integer q values in 1..10 for this instrument's live metadata."""
    try:
        q_min = D(str(event.quantity_minimum))
        q_inc = D(str(event.quantity_increment))
        p_inc = D(str(event.price_increment))
    except Exception:
        return ()
    if q_min <= ZERO or q_inc <= ZERO or p_inc <= ZERO:
        return ()
    return tuple(
        q
        for q in range(1, MAX_Q + 1)
        if D(q) >= q_min and _is_increment_multiple(D(q), q_inc)
    )


def metadata_dict(event: Any) -> dict[str, Any]:
    return {
        "quantity_minimum": str(getattr(event, "quantity_minimum", None)),
        "quantity_increment": str(getattr(event, "quantity_increment", None)),
        "price_increment": str(getattr(event, "price_increment", None)),
        "valid_integer_quantities": list(valid_integer_quantities(event)),
    }


def select_plan_for_event(
    event: Any,
    *,
    snap: Any,
    increment: D,
    min_q: int = policy.DEFAULT_MIN_Q,
    max_q: int = policy.DEFAULT_MAX_Q,
    max_loss: D = policy.DEFAULT_MAX_LOSS,
    edge_per_contract: D = policy.DEFAULT_EDGE_PER_CONTRACT,
):
    valid = [q for q in valid_integer_quantities(event) if min_q <= q <= max_q]
    for q in sorted(valid, reverse=True):
        plan = policy.plan_for_quantity(
            quantity=D(q),
            snap=snap,
            increment=increment,
            max_loss=max_loss,
            edge_per_contract=edge_per_contract,
        )
        if plan is not None:
            return plan
    return None


def event_contract_ok(event: Any) -> bool:
    """Install the live event's grid context and fail closed if no q1..q10 is valid."""
    global _current_valid_quantities, _current_metadata
    valid = valid_integer_quantities(event)
    try:
        meta = (
            D(str(event.quantity_minimum)),
            D(str(event.quantity_increment)),
            D(str(event.price_increment)),
        )
    except Exception:
        meta = None
    _current_valid_quantities = valid
    _current_metadata = meta
    return bool(valid)


def select_plan_grid_aware(
    *,
    snap: Any,
    increment: D,
    min_q: int = policy.DEFAULT_MIN_Q,
    max_q: int = policy.DEFAULT_MAX_Q,
    max_loss: D = policy.DEFAULT_MAX_LOSS,
    edge_per_contract: D = policy.DEFAULT_EDGE_PER_CONTRACT,
):
    """Drop-in policy.select_plan replacement using the current event grid context."""
    if _current_valid_quantities is None:
        return _ORIGINAL_SELECT_PLAN(
            snap=snap,
            increment=increment,
            min_q=min_q,
            max_q=max_q,
            max_loss=max_loss,
            edge_per_contract=edge_per_contract,
        )
    for q in sorted(
        (q for q in _current_valid_quantities if min_q <= q <= max_q),
        reverse=True,
    ):
        plan = policy.plan_for_quantity(
            quantity=D(q),
            snap=snap,
            increment=increment,
            max_loss=max_loss,
            edge_per_contract=edge_per_contract,
        )
        if plan is not None:
            return plan
    return None


def install() -> None:
    """Install frozen V5.3, then replace only contract-grid admission/planning."""
    v53.install()
    v53._event_contract_ok = event_contract_ok
    policy.select_plan = select_plan_grid_aware

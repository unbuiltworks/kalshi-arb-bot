"""Instrument-grid and catastrophic-loss adapter for Gemini adaptive maker V5.4.

The underlying adaptive executor remains the reviewed V5.3 implementation, but this
successor release adds two admission constraints before a plan may be used live:

1. Gemini prediction contracts publish instrument-specific quantityMinimum and
   quantityIncrement. We trade only integer clips q=1..10 that are valid on that
   actual grid.
2. Normal rescue availability is not a guaranteed loss cap: the book can gap between
   admission and fill detection. Therefore every possible first maker leg must also
   fit inside the configured campaign-loss ceiling even if rescue fails completely
   and the directional contract settles at zero.

Liquidity rewards and maker rebates are not used to relax either bound.
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


def single_leg_settlement_loss(*, price: D, quantity: D) -> D:
    """Conservative full loss if a filled YES/NO maker leg settles worthless."""
    p = D(str(price))
    q = D(str(quantity))
    return q * p + policy.gemini_predictions_maker_fee(p, q)


def catastrophic_loss_for_plan(plan: policy.AdaptiveMakerPlan) -> D:
    return max(
        single_leg_settlement_loss(price=plan.yes_quote, quantity=plan.quantity),
        single_leg_settlement_loss(price=plan.no_quote, quantity=plan.quantity),
    )


def plan_within_campaign_bound(
    plan: policy.AdaptiveMakerPlan,
    *,
    campaign_loss: D | None = None,
) -> bool:
    limit = v53.v5.max_campaign_loss() if campaign_loss is None else D(str(campaign_loss))
    return catastrophic_loss_for_plan(plan) <= limit


def _plan_for_valid_quantities(
    valid: list[int],
    *,
    snap: Any,
    increment: D,
    max_loss: D,
    edge_per_contract: D,
):
    for q in sorted(valid, reverse=True):
        plan = policy.plan_for_quantity(
            quantity=D(q),
            snap=snap,
            increment=increment,
            max_loss=max_loss,
            edge_per_contract=edge_per_contract,
        )
        if plan is not None and plan_within_campaign_bound(plan):
            return plan
    return None


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
    return _plan_for_valid_quantities(
        valid,
        snap=snap,
        increment=increment,
        max_loss=max_loss,
        edge_per_contract=edge_per_contract,
    )


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
    """Drop-in planner with contract-grid + worst-settlement campaign constraints."""
    if _current_valid_quantities is None:
        # Fail closed if no live event metadata has established the quantity grid.
        return None
    valid = [q for q in _current_valid_quantities if min_q <= q <= max_q]
    return _plan_for_valid_quantities(
        valid,
        snap=snap,
        increment=increment,
        max_loss=max_loss,
        edge_per_contract=edge_per_contract,
    )


def install() -> None:
    """Install adaptive V5.3 execution, then enforce V5.4 admission constraints."""
    v53.install()
    v53._event_contract_ok = event_contract_ok
    policy.select_plan = select_plan_grid_aware

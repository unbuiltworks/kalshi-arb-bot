from decimal import Decimal

import pytest

from src.kalshi_nested_rescue_guard import build_rescue_plan, fragment_min_pnl

D = Decimal


def test_three_cent_lock_can_spend_explicit_two_cent_rescue_budget():
    # Full lot: maker 95c + initial 1c hedge + conservative 1c fee = +3c.
    # With an explicit 2c max loss, the rescue planner may cross only as far as
    # the cent-level fee model still keeps P&L >= -2c.
    plan = build_rescue_plan(
        quantity=D("1.00"),
        maker_cost=D("0.95"),
        current_hedge_cost=D("0.01"),
        fee_multiplier=D("1"),
        max_rescue_loss=D("0.02"),
    )
    assert plan.rescue_limit_cost >= D("0.01")
    assert plan.pnl_at_rescue_limit >= D("-0.02")
    assert fragment_min_pnl(
        quantity=D("1.00"),
        maker_cost=D("0.95"),
        hedge_cost=plan.rescue_limit_cost + D("0.01"),
        fee_multiplier=D("1"),
    ) < D("-0.02")


def test_zero_loss_budget_never_intentionally_crosses_into_loss():
    plan = build_rescue_plan(
        quantity=D("1.00"),
        maker_cost=D("0.95"),
        current_hedge_cost=D("0.01"),
        fee_multiplier=D("1"),
        max_rescue_loss=D("0"),
    )
    assert plan.pnl_at_rescue_limit >= 0


def test_rescue_budget_is_absolute_for_fractional_fill():
    plan = build_rescue_plan(
        quantity=D("0.25"),
        maker_cost=D("0.95"),
        current_hedge_cost=D("0.01"),
        fee_multiplier=D("1"),
        max_rescue_loss=D("0.02"),
    )
    assert plan.quantity == D("0.25")
    assert plan.pnl_at_rescue_limit >= D("-0.02")


def test_fails_closed_if_current_hedge_already_breaks_budget():
    with pytest.raises(ValueError, match="already exceeds"):
        build_rescue_plan(
            quantity=D("1.00"),
            maker_cost=D("0.95"),
            current_hedge_cost=D("0.10"),
            fee_multiplier=D("1"),
            max_rescue_loss=D("0.02"),
        )


def test_no_negative_or_large_silent_loss_budget():
    with pytest.raises(ValueError, match=">= 0"):
        build_rescue_plan(
            quantity=D("1"), maker_cost=D("0.95"), current_hedge_cost=D("0.01"),
            fee_multiplier=D("1"), max_rescue_loss=D("-0.01"),
        )
    with pytest.raises(ValueError, match="hard ceiling"):
        build_rescue_plan(
            quantity=D("1"), maker_cost=D("0.95"), current_hedge_cost=D("0.01"),
            fee_multiplier=D("1"), max_rescue_loss=D("0.26"),
        )


def test_quantity_cannot_expand_past_first_probe_cap():
    with pytest.raises(ValueError, match="quantity"):
        build_rescue_plan(
            quantity=D("1.01"), maker_cost=D("0.95"), current_hedge_cost=D("0.01"),
            fee_multiplier=D("1"), max_rescue_loss=D("0.02"),
        )


def test_price_inputs_are_bounded_probabilities():
    with pytest.raises(ValueError, match="maker_cost"):
        build_rescue_plan(
            quantity=D("1"), maker_cost=D("1.00"), current_hedge_cost=D("0.01"),
            fee_multiplier=D("1"), max_rescue_loss=D("0.02"),
        )

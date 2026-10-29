"""These gates may only ever REFUSE. A test that lets one of them permit
something the deployed limits refused would be a test of a regression."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_validation_gates as g

FIVE = D("5")


def gates(**kw):
    return g.ValidationGates(**kw)


def allow(gt, **kw):
    base = dict(order_qty=D(5), order_price=D("0.49"),
                order_intent="ORDER_INTENT_BUY_LONG")
    base.update(kw)
    return gt.refuse(**base)


# ------------------------------------------------- unmatched inventory cap

def test_five_unmatched_is_allowed():
    assert allow(gates(), unmatched_after=D(5)) is None


def test_more_than_five_unmatched_is_refused():
    r = allow(gates(), unmatched_after=D("5.01"))
    assert r and r.startswith("UNMATCHED_CONTRACTS")


def test_the_cap_is_on_ABSOLUTE_inventory_either_side():
    r = allow(gates(), unmatched_after=D(-6))
    assert r and r.startswith("UNMATCHED_CONTRACTS")


def test_this_cap_is_tighter_than_the_deployed_residual_limit():
    """Deployed max_abs_residual is 10 and k=2 on a 5-clip permits 10."""
    assert gates().max_unmatched_contracts == FIVE
    assert allow(gates(), unmatched_after=D(10)) is not None


# ---------------------------------------------------- cumulative loss limit

def test_under_the_cumulative_limit_is_allowed():
    assert allow(gates(), cumulative_realized=D("-19.99")) is None


def test_at_the_cumulative_limit_is_refused():
    r = allow(gates(), cumulative_realized=D(-20))
    assert r and r.startswith("CUMULATIVE_LOSS_LIMIT")


def test_conservative_unrealized_loss_counts_toward_it():
    r = allow(gates(), cumulative_realized=D(-15),
              cumulative_unrealized_loss=D(5))
    assert r and r.startswith("CUMULATIVE_LOSS_LIMIT")


def test_a_profit_does_not_offset_into_headroom_above_zero():
    """A gain must not create negative 'loss' that buys extra room."""
    assert allow(gates(), cumulative_realized=D(100)) is None
    r = allow(gates(), cumulative_realized=D(100),
              cumulative_unrealized_loss=D(25))
    assert r and r.startswith("CUMULATIVE_LOSS_LIMIT"), \
        "a realized gain must not fund an unrealized loss past the ceiling"


def test_it_does_not_replace_the_five_dollar_daily_halt():
    """This is run-scoped and ADDITIVE; the daily halt lives in the deployed
    SafetyLimits and must still be able to refuse on its own."""
    gt = gates()
    assert gt.max_cumulative_loss_usd == D(20)
    assert g.combined_refusal("DAILY_REALIZED_LOSS_HALT", None) == \
        "DAILY_REALIZED_LOSS_HALT"


# ------------------------------------------------------------- cash gate

def test_a_single_cycle_is_within_the_cash_cap():
    assert allow(gates()) is None


def test_repeated_cycles_are_refused_once_cash_exceeds_the_cap():
    """The exact hazard: the loss gate reads 0 while cash keeps leaving."""
    legs = [{"cash_out": D("2.45"), "settled": False} for _ in range(4)]
    r = allow(gates(), filled_legs=legs)
    assert r and r.startswith("CASH_COMMITMENT"), \
        "four cycles push out $9.80 plus this order; that must refuse"


def test_no_recycling_is_the_default():
    assert gates().cash_policy == g.NO_RECYCLING


def test_a_closing_leg_does_not_free_cash_under_no_recycling():
    legs = [{"cash_out": D("2.45"), "settled": False},
            {"cash_out": D("-2.65"), "settled": False, "closing": True}]
    assert gates().cash_committed(legs) == D("2.45")


def test_a_closing_leg_frees_cash_only_under_the_verified_policy():
    legs = [{"cash_out": D("2.45"), "settled": False},
            {"cash_out": D("-2.65"), "settled": False, "closing": True}]
    gt = gates(cash_policy=g.RECYCLE_ON_COMPLETE)
    assert gt.cash_committed(legs) == D("-0.20")


def test_settlement_releases_cash_under_either_policy():
    legs = [{"cash_out": D("2.45"), "settled": True}]
    assert gates().cash_committed(legs) == D(0)


def test_an_unknown_cash_policy_is_rejected_at_construction():
    with pytest.raises(ValueError):
        gates(cash_policy="RECYCLE_BECAUSE_I_HOPE_SO")


# ------------------------------------------- pending orders count as cash

def test_a_resting_long_counts_qty_times_price():
    gt = gates()
    assert gt.pending_obligation(
        [{"qty": D(5), "price": D("0.40"),
          "intent": "ORDER_INTENT_BUY_LONG"}]) == D("2.00")


def test_a_resting_short_counts_its_full_dollar_obligation():
    """A resting BUY_SHORT of n shares owes up to $1 a share."""
    gt = gates()
    assert gt.pending_obligation(
        [{"qty": D(5), "price": D("0.10"),
          "intent": "ORDER_INTENT_BUY_SHORT"}]) == D(5)


def test_outstanding_orders_can_themselves_exhaust_the_cash_cap():
    out = [{"qty": D(5), "price": D("0.90"),
            "intent": "ORDER_INTENT_BUY_LONG"} for _ in range(3)]
    r = allow(gates(), outstanding=out)
    assert r and r.startswith("CASH_COMMITMENT")


def test_the_order_under_test_is_counted_as_if_filled_in_full():
    gt = gates(max_cash_deployed_usd=D("2.00"))
    r = allow(gt, order_qty=D(5), order_price=D("0.49"))
    assert r and r.startswith("CASH_COMMITMENT")


def test_a_short_order_under_test_is_counted_at_its_dollar_obligation():
    gt = gates(max_cash_deployed_usd=D("4.99"))
    r = allow(gt, order_intent="ORDER_INTENT_BUY_SHORT", order_price=D("0.01"))
    assert r and r.startswith("CASH_COMMITMENT")


# ------------------------------------------------ authoritative cash gate

def test_arming_is_refused_without_a_balance_reading():
    assert gates().require_cash_evidence(None) == "NO_AUTHORITATIVE_CASH_READING"


def test_an_empty_cash_anchor_is_refused_not_treated_as_unlimited():
    """This is the live state today: cash_anchor is {}."""
    assert gates().require_cash_evidence({}) == "NO_AUTHORITATIVE_CASH_READING"


def test_a_positive_balance_passes():
    assert gates().require_cash_evidence({"available": "50.00"}) is None


def test_a_non_positive_balance_is_refused():
    assert gates().require_cash_evidence({"available": "0"}) == \
        "NON_POSITIVE_AVAILABLE_CASH"


def test_a_balance_without_an_available_field_is_refused():
    assert gates().require_cash_evidence({"somethingElse": 1}) == \
        "BALANCE_READING_HAS_NO_AVAILABLE_FIELD"


def test_a_known_balance_binds_even_below_the_configured_cap():
    r = allow(gates(), available_cash_usd=D("1.00"))
    assert r and r.startswith("INSUFFICIENT_AVAILABLE_CASH")


# ----------------------------------------------------------- window budget

def test_window_budget_is_enforced():
    r = allow(gates(max_windows=20), windows_used=20)
    assert r == "WINDOW_BUDGET_EXHAUSTED:20>=20"


def test_within_the_window_budget_is_allowed():
    assert allow(gates(max_windows=20), windows_used=19) is None


# --------------------------------------------- these gates only ever refuse

def test_a_deployed_refusal_is_never_masked():
    assert g.combined_refusal("MAKER_ONLY_NO_TAKER", None) == \
        "MAKER_ONLY_NO_TAKER"
    assert g.combined_refusal("MAKER_ONLY_NO_TAKER", "CASH_COMMITMENT:x") == \
        "MAKER_ONLY_NO_TAKER"


def test_a_validation_refusal_applies_when_the_deployed_limits_allow():
    assert g.combined_refusal(None, "CASH_COMMITMENT:x") == "CASH_COMMITMENT:x"


def test_both_allowing_allows():
    assert g.combined_refusal(None, None) is None


def test_gates_report_themselves_as_additive():
    assert "ADDITIVE" in gates().as_dict()["labels"]

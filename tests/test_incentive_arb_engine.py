from decimal import Decimal as D

import pytest

from src.incentive_arb_engine import (
    RewardPayment,
    accounting_summary,
    evaluate_pair,
    inventory_risk,
    sizing_decision,
)


def test_sub_dollar_pair_is_arb_without_reward_credit():
    result = evaluate_pair(venue="POLYMARKET", yes_price="0.42", no_price="0.55", quantity="100")
    assert result.is_arb is True
    assert result.locked_edge == D("3.00")
    assert result.locked_edge_per_pair == D("0.03")
    assert result.classification == "COMPLETE_SET_ARB"
    assert result.reward_credit_used_for_arb == D("0")


def test_fees_can_remove_apparent_sub_dollar_arb():
    result = evaluate_pair(
        venue="POLYMARKET",
        yes_price="0.42",
        no_price="0.55",
        quantity="100",
        no_fee="3.25",
    )
    assert result.is_arb is False
    assert result.locked_edge == D("-0.25")
    assert result.classification == "COMPLETE_SET_NEGATIVE"


def test_rewards_never_rescue_negative_pair_into_arb_bucket():
    pair = evaluate_pair(venue="GEMINI", yes_price="0.51", no_price="0.50", quantity="100")
    acct = accounting_summary(
        trade_settlement_realized=pair.locked_edge,
        rewards=[RewardPayment("MAKER_REBATE", D("5"), "PAID")],
    )
    assert pair.is_arb is False
    assert pair.locked_edge == D("-1.00")
    assert acct.realized_net == D("4.00")
    assert acct.paid_rewards == D("5")


def test_projected_reward_is_not_realized():
    acct = accounting_summary(
        trade_settlement_realized="1.25",
        rewards=[
            RewardPayment("MAKER_REBATE", D("2.00"), "PAID"),
            RewardPayment("LIQUIDITY_REWARD", D("7.00"), "PROJECTED"),
        ],
    )
    assert acct.realized_net == D("3.25")
    assert acct.projected_rewards == D("7.00")


def test_inventory_separates_paired_and_residual_capital():
    risk = inventory_risk(yes_qty="100", no_qty="80", yes_vwap="0.42", no_vwap="0.55")
    assert risk.matched_qty == D("80")
    assert risk.paired_acquisition_cost == D("77.60")
    assert risk.residual_side == "YES"
    assert risk.residual_qty == D("20")
    assert risk.residual_acquisition_cost == D("8.40")


def test_kalshi_uses_roundtrip_label_not_complete_set_label():
    result = evaluate_pair(venue="KALSHI", yes_price="0.40", no_price="0.55", quantity="10")
    assert result.is_arb is True
    assert result.classification == "LOCKED_ROUNDTRIP_ARB"
    assert result.locked_edge == D("0.50")


def test_pair_completion_gets_priority_and_respects_budget():
    decision = sizing_decision(
        candidate_quantity="50",
        candidate_unit_cost="0.55",
        completes_residual=True,
        locked_edge_per_pair="0.03",
        pair_floor="0.02",
        current_residual_qty="30",
        residual_unit_cap="50",
        paired_capital_remaining="11",
    )
    assert decision.mode == "PAIR_COMPLETE"
    assert decision.allowed_quantity == D("20")


def test_new_inventory_is_blocked_at_residual_cap():
    decision = sizing_decision(
        candidate_quantity="10",
        candidate_unit_cost="0.40",
        completes_residual=False,
        locked_edge_per_pair=None,
        pair_floor="0.02",
        current_residual_qty="5",
        residual_unit_cap="5",
        paired_capital_remaining="100",
    )
    assert decision.mode == "RESIDUAL_CAP"
    assert decision.allowed_quantity == D("0")


def test_negative_fee_rejected_so_rebate_cannot_be_smuggled_into_arb_math():
    with pytest.raises(ValueError):
        evaluate_pair(venue="GEMINI", yes_price="0.50", no_price="0.50", yes_fee="-0.01")

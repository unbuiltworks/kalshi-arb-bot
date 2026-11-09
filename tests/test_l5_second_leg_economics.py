"""Deterministic accounting tests for the read-only counterfactual engine."""
from decimal import Decimal as D
import pytest
from src.l5_second_leg_economics import Inventory, Candidate, evaluate


def c(action="TAKER", side="DOWN", qty="1", px="0.35", fee="0",
      depth="1", age="0.2", adverse="0", probability=None):
    return Candidate(action, side, D(qty), D(px), D(fee), D(depth),
                     D(age), D(adverse),
                     None if probability is None else D(probability))


def test_unequal_inventory_settlement_accounting():
    inv = Inventory(D("2"), D("1"), D("1.59"))
    assert inv.outcome_net() == (D("0.41"), D("-0.59"))


def test_completion_improves_worst_case_but_not_necessarily_profitable():
    inv = Inventory(D("1"), D("0"), D("0.62"))
    out = evaluate(inv, c(px="0.35"))
    assert out["eligible"]
    assert out["fill_worst"] == "0.03"
    assert out["worst_improvement"] == "0.65"


def test_expensive_completion_can_worsen_worst_case():
    inv = Inventory(D("1"), D("0"), D("0.62"))
    out = evaluate(inv, c(px="0.95"))
    assert out["worst_improvement"] == "0.05"
    assert D(out["fill_worst"]) < 0


def test_fees_and_adverse_selection_are_charged():
    inv = Inventory(D("1"), D("0"), D("0.62"))
    out = evaluate(inv, c(fee="0.02", adverse="0.02"))
    assert out["fill_worst"] == "-0.01"


@pytest.mark.parametrize("kwargs,reason", [
    ({"age":"2.01"}, "STALE_BOOK"),
    ({"depth":"0.9"}, "INSUFFICIENT_DEPTH"),
])
def test_invalid_execution_conditions_refuse(kwargs, reason):
    inv = Inventory(D("1"), D("0"), D("0.62"))
    assert evaluate(inv, c(**kwargs))["reason"] == reason


def test_capital_and_loss_limits_refuse():
    inv = Inventory(D("1"), D("0"), D("0.62"))
    assert evaluate(inv, c(), cash_headroom=D("0.30"))["reason"] == "CASH_HEADROOM"
    assert evaluate(inv, c(px="0.9"), worst_case_loss_ceiling=D("0.1"))["reason"] == "WORST_CASE_LIMIT"


def test_maker_no_fill_is_not_assumed():
    inv = Inventory(D("1"), D("0"), D("0.62"))
    out = evaluate(inv, c(action="MAKER"))
    assert out["eligible"] and out["model_expected_worst"] is None
    assert out["no_fill_worst"] == "-0.62"


def test_explicit_probability_is_model_only():
    inv = Inventory(D("1"), D("0"), D("0.62"))
    out = evaluate(inv, c(action="REPRICE", probability="0.5"))
    assert out["model_only"]
    assert out["model_expected_worst"] == "-0.295"


def test_reject_invalid_inventory():
    with pytest.raises(ValueError):
        Inventory(D("-1"), D("0"), D("0"))


def test_20261109_1530z_settled_residual_and_expensive_completion():
    """Observed 0.7 DOWN @0.44; hypothetical UP @0.60 before fees."""
    inv = Inventory(D("0"), D("0.7"), D("0.308"))
    assert inv.outcome_net() == (D("-0.308"), D("0.392"))
    out = evaluate(inv, c(side="UP", qty="0.7", px="0.60", depth="0.7"))
    assert out["eligible"]
    assert D(out["spend"]) == D("0.420")
    assert D(out["fill_worst"]) == D("-0.028")
    assert D(out["worst_improvement"]) == D("0.280")
    assert D(out["fill_worst"]) < 0  # Hedging risk != a profitable pair.


from src.l5_second_leg_economics import evaluate_paired_entry


def test_paired_entry_requires_calibrated_joint_fills():
    out = evaluate_paired_entry(up_price="0.51", down_price="0.44", quantity="1")
    assert not out["eligible"]
    assert out["reason"] == "JOINT_FILL_OR_SETTLEMENT_UNVALIDATED"


def test_profitable_matched_pair_can_still_have_negative_expectancy():
    out = evaluate_paired_entry(up_price="0.51", down_price="0.44", quantity="1",
        p_both="0.1", p_up_only="0", p_down_only="0.8", p_neither="0.1",
        p_up_settles_given_up_only="0.6", p_up_settles_given_down_only="0.6")
    assert not out["eligible"]
    assert out["reason"] == "NONPOSITIVE_EXPECTED_NET"
    assert D(out["both_net"]) == D("0.05")
    assert D(out["down_only_expected_net"]) == D("-0.04")
    assert D(out["expected_net"]) == D("-0.027")
    assert D(out["p_any_residual"]) == D("0.8")


def test_paired_entry_rejects_independent_unjustified_probabilities():
    with pytest.raises(ValueError, match="sum to one"):
        evaluate_paired_entry(up_price="0.51", down_price="0.44", quantity="1",
            p_both="0.5", p_up_only="0.5", p_down_only="0.5",
            p_neither="0", p_up_settles_given_up_only="0.6", p_up_settles_given_down_only="0.6")


def test_paired_entry_pair_cost_limit():
    out = evaluate_paired_entry(up_price="0.60", down_price="0.44", quantity="1",
        max_combined_cost="0.96")
    assert not out["eligible"] and out["reason"] == "PAIR_COST_LIMIT"


def test_adverse_selection_conditioning_changes_expected_net():
    # Same marginal quote economics; DOWN-only fills predict UP settlement.
    out = evaluate_paired_entry(up_price="0.51", down_price="0.44", quantity="1",
        p_both="0.1", p_up_only="0", p_down_only="0.8", p_neither="0.1",
        p_up_settles_given_up_only="0.5", p_up_settles_given_down_only="0.9")
    assert not out["eligible"]
    assert D(out["expected_net"]) == D("-0.267")


def test_missing_conditional_settlement_probability_refuses():
    out = evaluate_paired_entry(up_price="0.51", down_price="0.44", quantity="1",
        p_both="0.1", p_up_only="0", p_down_only="0.8", p_neither="0.1",
        p_up_settles_given_up_only="0.5")
    assert not out["eligible"]


def test_paired_entry_positive_expected_net_passes_model_gate_only():
    out = evaluate_paired_entry(up_price="0.46", down_price="0.45", quantity="1",
        p_both="0.8", p_up_only="0.05", p_down_only="0.05", p_neither="0.1",
        p_up_settles_given_up_only="0.6",
        p_up_settles_given_down_only="0.4", minimum_expected_net="0.01")
    assert out["eligible"] and out["model_only"]
    assert D(out["expected_net"]) == D("0.0845")


def test_positive_pair_profit_but_negative_total_ev_refuses():
    out = evaluate_paired_entry(up_price="0.51", down_price="0.44", quantity="1",
        p_both="0.1", p_up_only="0", p_down_only="0.8", p_neither="0.1",
        p_up_settles_given_up_only="0.5",
        p_up_settles_given_down_only="0.9")
    assert not out["eligible"] and out["reason"] == "NONPOSITIVE_EXPECTED_NET"

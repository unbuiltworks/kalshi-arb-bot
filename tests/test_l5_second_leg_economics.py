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
    assert not out["eligible"] and out["model_expected_worst"] is None
    assert out["reason"] == "FILL_PROBABILITY_UNVALIDATED"
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
    """A distribution that does not sum to one is REFUSED, not raised.

    This test asserted the raise. It now asserts the property -- the input
    is rejected and nothing is eligible -- because a controller asking
    "should I enter?" must receive "no", not an exception it has to catch.
    Independent maker fills multiplied out to 0.5/0.5/0.5/0 still fail.
    """
    out = evaluate_paired_entry(up_price="0.51", down_price="0.44", quantity="1",
        p_both="0.5", p_up_only="0.5", p_down_only="0.5",
        p_neither="0", p_up_settles_given_up_only="0.6", p_up_settles_given_down_only="0.6")
    assert not out["eligible"]
    assert out["reason"] == "JOINT_FILL_DISTRIBUTION_NOT_NORMALISED"
    assert D(out["probability_sum"]) == D("1.5")
    assert "expected_net" not in out, (
        "an unnormalised distribution must not yield an expected value")


def test_a_rounding_residue_in_a_calibrated_distribution_is_tolerated():
    """Calibrated frequencies are rounded. 0.3333 x3 + 0.0001 sums to one
    exactly; a residue inside the tolerance must not read as a mismatch."""
    out = evaluate_paired_entry(up_price="0.48", down_price="0.47", quantity="1",
        p_both="0.3333", p_up_only="0.3333", p_down_only="0.3333",
        p_neither="0.0002", p_up_settles_given_up_only="0.5",
        p_up_settles_given_down_only="0.5")
    assert out["reason"] != "JOINT_FILL_DISTRIBUTION_NOT_NORMALISED"


def test_a_real_mismatch_outside_the_tolerance_still_refuses():
    out = evaluate_paired_entry(up_price="0.48", down_price="0.47", quantity="1",
        p_both="0.10", p_up_only="0.35", p_down_only="0.35", p_neither="0.19",
        p_up_settles_given_up_only="0.5", p_up_settles_given_down_only="0.5")
    assert not out["eligible"]
    assert out["reason"] == "JOINT_FILL_DISTRIBUTION_NOT_NORMALISED"
    assert D(out["probability_sum"]) == D("0.99")


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
    assert D(out["expected_net"]) == D("0.0865")


def test_positive_pair_profit_but_negative_total_ev_refuses():
    out = evaluate_paired_entry(up_price="0.51", down_price="0.44", quantity="1",
        p_both="0.1", p_up_only="0", p_down_only="0.8", p_neither="0.1",
        p_up_settles_given_up_only="0.5",
        p_up_settles_given_down_only="0.9")
    assert not out["eligible"] and out["reason"] == "NONPOSITIVE_EXPECTED_NET"


def test_residual_probability_gate_refuses_high_single_leg_risk():
    out = evaluate_paired_entry(up_price="0.51", down_price="0.44", quantity="1",
        p_both="0.1", p_up_only="0", p_down_only="0.8", p_neither="0.1",
        p_up_settles_given_up_only="0.5",
        p_up_settles_given_down_only="0.9", max_residual_probability="0.25")
    assert not out["eligible"]
    assert out["reason"] == "RESIDUAL_PROBABILITY_LIMIT"


def test_single_leg_worst_case_gate_refuses_even_with_positive_model_ev():
    out = evaluate_paired_entry(up_price="0.46", down_price="0.45", quantity="1",
        p_both="0.8", p_up_only="0.05", p_down_only="0.05", p_neither="0.1",
        p_up_settles_given_up_only="0.6",
        p_up_settles_given_down_only="0.4", max_single_leg_loss="0.40")
    assert not out["eligible"]
    assert out["reason"] == "SINGLE_LEG_LOSS_LIMIT"
    assert D(out["residual_worst"]) == D("-0.46")


def test_adverse_cost_charged_when_only_one_leg_fills():
    out = evaluate_paired_entry(up_price="0.46", down_price="0.45", quantity="1",
        p_both="0", p_up_only="1", p_down_only="0", p_neither="0",
        p_up_settles_given_up_only="0.6",
        p_up_settles_given_down_only="0.4", adverse_cost="0.03")
    assert D(out["up_only_expected_net"]) == D("0.11")


def test_taker_hypothesis_never_claims_venue_execution():
    inv = Inventory(D("1"), D("0"), D("0.62"))
    out = evaluate(inv, c(action="TAKER"))
    assert out["eligible"]
    assert out["execution_confirmed"] is False


def test_profit_floor_must_be_nonnegative():
    with pytest.raises(ValueError, match="negative minimum expected net"):
        evaluate_paired_entry(up_price="0.46", down_price="0.45", quantity="1",
            p_both="1", p_up_only="0", p_down_only="0", p_neither="0",
            p_up_settles_given_up_only="0.5",
            p_up_settles_given_down_only="0.5", minimum_expected_net="-0.01")

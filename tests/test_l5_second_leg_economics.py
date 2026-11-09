"""Deterministic accounting tests for the read-only counterfactual engine."""
from decimal import Decimal as D
import pytest
from src.l5_second_leg_economics import Inventory, Candidate, evaluate


def c(action="TAKER", side="DOWN", qty="1", px="0.35", fee="0",
      depth="1", age="0.2", adverse="0", probability=None):
    return Candidate(action, side, D(qty), D(px), D(fee), D(depth),
                     D(age), D(adverse),
                     None if probability is None else D(probability))


def test_actual_historical_one_pair_and_residual_accounting():
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

"""No-network tests for probability-aware inventory action comparisons."""
import importlib.util
import pathlib
import sys
import pytest

spec = importlib.util.spec_from_file_location("btc15m_portfolio_decisions",
    pathlib.Path("analysis/l5/btc15m_portfolio_decisions.py"))
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

def inv(up=1, down=0, cost=.55, cash=5):
    return m.Inventory(up, down, cost, cash)

def buy(side, price, **kw):
    return m.Candidate("BUY", side, price, **kw)

def test_completion_reduces_residual_without_calibration():
    r = m.evaluate(inv(), buy("DOWN", .25), .7, False)
    assert r["eligible"] and r["candidate_better_than_hold"]
    assert r["filled_up_payoff"] == pytest.approx(.20)
    assert r["filled_down_payoff"] == pytest.approx(.20)

def test_uncalibrated_directional_refused():
    r = m.evaluate(inv(0, 0, 0), buy("DOWN", .2), .3, False)
    assert not r["eligible"] and r["reason"] == "UNCALIBRATED_DIRECTIONAL"

def test_calibrated_positive_edge_candidate():
    r = m.evaluate(inv(0, 0, 0), buy("DOWN", .2), .7, True, .05)
    assert r["expected_increment_vs_hold"] == pytest.approx(.05)
    assert r["candidate_better_than_hold"]
    assert not r["live_eligible"]

def test_cost_and_uncertainty_reverse_edge():
    r = m.evaluate(inv(0, 0, 0), buy("DOWN", .2, execution_cost=.05), .7, True, .1)
    assert not r["candidate_better_than_hold"]

def test_fill_probability_scales_expected_edge():
    r = m.evaluate(inv(0, 0, 0), buy("DOWN", .2, fill_probability=.25), .7, True)
    assert r["expected_increment_vs_hold"] == pytest.approx(.025)

def test_residual_cap_blocks_accumulation():
    r = m.evaluate(inv(), buy("UP", .2), .8, True)
    assert r["reason"] == "RESIDUAL_CAP"

def test_cash_blocks():
    r = m.evaluate(inv(cash=.1), buy("DOWN", .2), .7, True)
    assert r["reason"] == "BUDGET"

def test_full_payoffs_not_just_increment():
    r = m.evaluate(inv(), buy("DOWN", .3), .7, False)
    assert r["worst_case_filled"] == pytest.approx(.15)

def test_completion_can_reduce_tail_loss_despite_negative_ev():
    r = m.evaluate(inv(), buy("DOWN", .35), .7, False)
    assert r["eligible"]
    assert not r["candidate_better_than_hold"]
    assert r["reduces_unmatched_inventory"]
    assert r["worst_case_improvement_if_filled"] == pytest.approx(.65)

def test_zero_quantity_buy_refused():
    with pytest.raises(ValueError, match="positive"):
        m.evaluate(inv(), buy("DOWN", .25, qty=0), .7, False)

def test_boolean_quantity_refused():
    with pytest.raises(ValueError, match="integer"):
        m.evaluate(inv(), buy("DOWN", .25, qty=True), .7, False)

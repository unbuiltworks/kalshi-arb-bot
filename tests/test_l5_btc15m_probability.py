"""Deterministic, network-free tests for the research-only BTC15m baseline."""
import importlib.util
import pathlib
import pytest

spec = importlib.util.spec_from_file_location("btc15m_probability",
    pathlib.Path("analysis/l5/btc15m_probability.py"))
m = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = m
spec.loader.exec_module(m)

def snap(**changes):
    d = dict(spot=100.0, strike=100.0, seconds_left=60.0,
             sigma_per_sqrt_second=0.001, spot_timestamp=100.0,
             decision_timestamp=101.0)
    d.update(changes)
    return m.Snapshot(**d)

def test_at_strike_near_half():
    assert 0.49 < m.estimate_up(snap()) < 0.51

def test_monotone_spot():
    assert m.estimate_up(snap(spot=102)) > m.estimate_up(snap(spot=100))
    assert m.estimate_up(snap(spot=100)) > m.estimate_up(snap(spot=98))

def test_complement():
    s = snap()
    u = m.assess_underdog(s, "UP", 0.2)
    d = m.assess_underdog(s, "DOWN", 0.2)
    assert u["probability"] + d["probability"] == pytest.approx(1)

@pytest.mark.parametrize("changes", [
    {"spot": 0}, {"strike": -1}, {"seconds_left": 0},
    {"sigma_per_sqrt_second": 0}, {"spot_timestamp": 102},
    {"spot_timestamp": 90}, {"spot": float("nan")},
])
def test_invalid_and_stale_rejected(changes):
    with pytest.raises(ValueError):
        m.estimate_up(snap(**changes))

def test_cost_and_margin_can_remove_candidate():
    s = snap()
    assert m.assess_underdog(s, "DOWN", 0.2)["candidate_only"]
    assert not m.assess_underdog(s, "DOWN", 0.2, 0.05, 0.3)["candidate_only"]

def test_no_live_authorization():
    r = m.assess_underdog(snap(), "DOWN", 0.2)
    assert r["live_eligible"] is False
    assert r["calibrated"] is False

def test_invalid_side():
    with pytest.raises(ValueError):
        m.assess_underdog(snap(), "YES", 0.2)

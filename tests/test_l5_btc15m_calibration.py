"""Tests for causal, window-level calibration comparison (no network/corpus)."""
import importlib.util
import pathlib
import sys
import pytest

spec = importlib.util.spec_from_file_location("btc15m_calibration",
    pathlib.Path("analysis/l5/btc15m_calibration.py"))
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

def series(p, n=100):
    return [m.WindowPrediction(i * 900, p if i % 2 else 1-p, bool(i % 2))
            for i in range(n)]

def test_perfect_scores_zero():
    r = m.metrics(series(1.0))
    assert r["brier"] == pytest.approx(0)
    assert r["log_loss"] == pytest.approx(0)

def test_better_forecast_scores_win():
    with pytest.raises(ValueError, match="insufficient"):
        m.chronological_gate(series(.8), series(.5))

def test_gate_requires_enough_oos_windows():
    with pytest.raises(ValueError, match="insufficient"):
        m.chronological_gate(series(.8), series(.5))

def test_better_forecast_with_explicit_minimum():
    r = m.chronological_gate(series(.8), series(.5), minimum_test_windows=40)
    assert r["forecast_better_on_both_scores"]
    assert r["brier_improvement"] > 0
    assert not r["live_eligible"]
    assert not r["profitability_established"]

def test_reject_duplicate_windows():
    x = series(.5)
    x[1] = x[0]
    with pytest.raises(ValueError, match="chronological"):
        m.metrics(x)

def test_reject_misaligned_outcomes():
    x = series(.8)
    y = series(.5)
    y[20] = m.WindowPrediction(y[20].window_start, .5, True)
    with pytest.raises(ValueError, match="differ"):
        m.chronological_gate(x, y, minimum_test_windows=40)

def test_reject_nonfinite():
    x = series(.8)
    x[0] = m.WindowPrediction(0, float("nan"), False)
    with pytest.raises(ValueError, match="probability"):
        m.metrics(x)

def test_reject_outcome_not_bool():
    x = series(.8)
    x[0] = m.WindowPrediction(0, .5, 1)
    with pytest.raises(ValueError, match="bool"):
        m.metrics(x)

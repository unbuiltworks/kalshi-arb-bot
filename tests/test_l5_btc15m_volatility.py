"""Research-only causal volatility estimation, no corpus or network."""
import importlib.util
import math
import pathlib
import sys
import pytest

spec = importlib.util.spec_from_file_location("btc15m_volatility",
    pathlib.Path("analysis/l5/btc15m_volatility.py"))
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

def ticks(n=91):
    return [m.Tick(float(i * 10), 100 * math.exp(0.0002 * i +
            0.001 * (1 if i % 2 else -1))) for i in range(n)]

def test_positive_causal_estimate():
    assert m.realized_sigma(ticks(), 900) > 0

def test_future_data_fails_closed():
    with pytest.raises(ValueError, match="future"):
        m.realized_sigma(ticks(92), 900)

def test_stale_latest_fails_closed():
    with pytest.raises(ValueError, match="stale"):
        m.realized_sigma(ticks(90), 900)

def test_gapped_history_fails_closed():
    xs = ticks()
    del xs[20:30]
    with pytest.raises(ValueError, match="missing interval"):
        m.realized_sigma(xs, 900)

def test_sparse_fails_closed():
    with pytest.raises(ValueError, match="insufficient"):
        m.realized_sigma(ticks(10), 90)

def test_unordered_fails_closed():
    xs = ticks()
    xs[2], xs[3] = xs[3], xs[2]
    with pytest.raises(ValueError, match="unordered"):
        m.realized_sigma(xs, 900)

def test_constant_prices_not_fake_signal():
    xs = [m.Tick(float(i * 10), 100) for i in range(91)]
    with pytest.raises(ValueError, match="zero"):
        m.realized_sigma(xs, 900)

def test_no_future_truncation():
    # Future ticks must be rejected, never silently dropped.
    with pytest.raises(ValueError, match="future"):
        m.realized_sigma(ticks(91) + [m.Tick(901, 100)], 900)

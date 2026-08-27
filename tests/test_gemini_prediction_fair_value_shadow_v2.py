from collections import deque
import math

from src.gemini_prediction_fair_value_shadow_v2 import (
    conservative_sigma,
    lagged_rms_sigma_per_s,
)


def _series(prices, step_ms=1000):
    return deque((i * step_ms, p) for i, p in enumerate(prices))


def test_lagged_rms_rejects_short_warmup():
    sigma, n = lagged_rms_sigma_per_s(_series([100 + i * 0.01 for i in range(20)]), lag_seconds=30)
    assert sigma is None
    assert n == 0


def test_lagged_rms_captures_sustained_move():
    prices = [100.0 * math.exp(0.00008 * i) for i in range(100)]
    sigma, n = lagged_rms_sigma_per_s(_series(prices), lookback_seconds=90, lag_seconds=30)
    assert sigma is not None
    assert sigma > 0
    assert n >= 12


def test_conservative_sigma_uses_larger_component():
    prices = []
    p = 100.0
    for i in range(120):
        p *= math.exp(0.00003 + (0.00001 if i % 2 else -0.00001))
        prices.append(p)
    sigma, meta = conservative_sigma(_series(prices), lookback_seconds=100, lag_seconds=30)
    assert sigma is not None
    vals = [x for x in (meta["sigma_consecutive"], meta["sigma_lagged_rms"]) if x is not None]
    assert sigma == max(vals)
    assert meta["sigma_method"] == "max(consecutive_std,lagged_rms)"

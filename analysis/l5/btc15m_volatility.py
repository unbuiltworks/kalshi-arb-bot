#!/usr/bin/env python3
"""Causal BTC spot-volatility estimator for the research-only BTC15m model.

Ticks must be from the SAME reference feed used for the market's strike and
settlement, in quote units of that feed. This module performs no I/O.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Sequence

@dataclass(frozen=True)
class Tick:
    timestamp: float
    price: float

def realized_sigma(ticks: Sequence[Tick], decision_timestamp: float,
                   lookback_seconds: float = 900.0,
                   max_latest_age_seconds: float = 2.0,
                   min_returns: int = 30,
                   max_gap_seconds: float = 30.0) -> float:
    """Return log-return volatility per sqrt(second), or fail closed.

    Uses only ticks <= decision time, requires enough independent intervals,
    refuses nonmonotone/duplicate timestamps and material missing-data gaps.
    No lookahead, interpolation, default volatility or probability fallback.
    """
    args = (decision_timestamp, lookback_seconds, max_latest_age_seconds,
            max_gap_seconds)
    if not all(math.isfinite(x) for x in args):
        raise ValueError("nonfinite configuration")
    if lookback_seconds <= 0 or max_latest_age_seconds < 0 or max_gap_seconds <= 0:
        raise ValueError("invalid configuration")
    if min_returns < 2 or isinstance(min_returns, bool):
        raise ValueError("min_returns must be >= 2")
    if not ticks:
        raise ValueError("no ticks")
    start = decision_timestamp - lookback_seconds
    selected = []
    previous_time = -math.inf
    for tick in ticks:
        if not math.isfinite(tick.timestamp) or not math.isfinite(tick.price):
            raise ValueError("nonfinite tick")
        if tick.price <= 0 or tick.timestamp <= previous_time:
            raise ValueError("nonpositive price or unordered timestamps")
        previous_time = tick.timestamp
        if tick.timestamp > decision_timestamp:
            raise ValueError("future tick in input")
        if tick.timestamp >= start:
            selected.append(tick)
    if len(selected) - 1 < min_returns:
        raise ValueError("insufficient returns")
    if decision_timestamp - selected[-1].timestamp > max_latest_age_seconds:
        raise ValueError("stale latest tick")
    if selected[0].timestamp - start > max_gap_seconds:
        raise ValueError("missing start of lookback")
    increments = []
    for a, b in zip(selected, selected[1:]):
        dt = b.timestamp - a.timestamp
        if dt > max_gap_seconds:
            raise ValueError("missing interval")
        increments.append((math.log(b.price / a.price), dt))
    # Drift estimated per second, then squared residuals scaled by elapsed
    # time. This is a historical realized-volatility estimate, not a forecast.
    drift = sum(r for r, _ in increments) / sum(dt for _, dt in increments)
    variance_rate = sum((r - drift * dt) ** 2 for r, dt in increments) / sum(
        dt for _, dt in increments)
    sigma = math.sqrt(variance_rate)
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError("zero or invalid realized volatility")
    return sigma

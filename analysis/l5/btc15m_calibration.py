#!/usr/bin/env python3
"""Research-only chronological probability calibration gate; no market orders.

Each observation represents ONE resolved 15-minute market, not a trade.
Predictions must have been recorded as-of their decision timestamp; callers
must never compute historical predictions using later spot or volatility.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Sequence

@dataclass(frozen=True)
class WindowPrediction:
    window_start: int
    predicted_up: float
    settled_up: bool

def _validate(rows: Sequence[WindowPrediction]) -> None:
    if not rows:
        raise ValueError("no windows")
    last = -math.inf
    for row in rows:
        if row.window_start <= last:
            raise ValueError("windows must be strictly chronological and unique")
        last = row.window_start
        if not math.isfinite(row.predicted_up) or not 0 <= row.predicted_up <= 1:
            raise ValueError("invalid probability")
        if type(row.settled_up) is not bool:
            raise ValueError("settled_up must be bool")

def metrics(rows: Sequence[WindowPrediction]) -> dict:
    """Window-weighted proper scores and calibration bias."""
    _validate(rows)
    eps = 1e-12
    brier = sum((r.predicted_up - float(r.settled_up)) ** 2 for r in rows) / len(rows)
    loss = -sum(float(r.settled_up) * math.log(max(eps, r.predicted_up)) +
                (1.0 - float(r.settled_up)) *
                math.log(max(eps, 1.0 - r.predicted_up)) for r in rows) / len(rows)
    bias = sum(r.predicted_up - float(r.settled_up) for r in rows) / len(rows)
    return {"windows": len(rows), "brier": brier, "log_loss": loss,
            "mean_bias": bias}

def chronological_gate(candidate: Sequence[WindowPrediction],
                       baseline: Sequence[WindowPrediction],
                       train_fraction: float = 0.6,
                       minimum_test_windows: int = 50) -> dict:
    """Compare frozen OOS probabilities on identical windows.

    Gate is deliberately conservative: this checks forecast scores ONLY;
    it never authorizes directional orders or asserts executable edge.
    """
    _validate(candidate)
    _validate(baseline)
    if not 0 < train_fraction < 1 or not math.isfinite(train_fraction):
        raise ValueError("invalid split")
    if minimum_test_windows < 1:
        raise ValueError("invalid test minimum")
    if [(r.window_start, r.settled_up) for r in candidate] != [
            (r.window_start, r.settled_up) for r in baseline]:
        raise ValueError("candidate and baseline windows/outcomes differ")
    split = int(len(candidate) * train_fraction)
    if split < 1 or len(candidate) - split < minimum_test_windows:
        raise ValueError("insufficient chronological test windows")
    c = metrics(candidate[split:])
    b = metrics(baseline[split:])
    return {"train_windows": split, "test_windows": len(candidate) - split,
            "candidate": c, "baseline": b,
            "brier_improvement": b["brier"] - c["brier"],
            "log_loss_improvement": b["log_loss"] - c["log_loss"],
            "forecast_better_on_both_scores":
                c["brier"] < b["brier"] and c["log_loss"] < b["log_loss"],
            "live_eligible": False, "profitability_established": False}

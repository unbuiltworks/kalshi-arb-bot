#!/usr/bin/env python3
"""Read-only BTC15m probability estimator. No network calls, orders or credentials.

Inputs MUST be synchronized to the market's resolution reference and available
as-of decision time. Spot/strike units match; volatility is per sqrt(second).
A market price is never an input to the probability estimate.
"""
from __future__ import annotations
import math
from dataclasses import dataclass

@dataclass(frozen=True)
class Snapshot:
    spot: float
    strike: float
    seconds_left: float
    sigma_per_sqrt_second: float
    spot_timestamp: float
    decision_timestamp: float
    max_staleness_seconds: float = 2.0

def estimate_up(s: Snapshot) -> float:
    """Zero-drift lognormal baseline, NOT a calibrated trading signal.

    At expiry ties are unresolved here: fail closed instead of guessing.
    This is a research-only baseline; microstructure and reference-source
    mismatches may invalidate its assumptions.
    """
    vals = (s.spot, s.strike, s.seconds_left, s.sigma_per_sqrt_second,
            s.spot_timestamp, s.decision_timestamp, s.max_staleness_seconds)
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("nonfinite input")
    if s.spot <= 0 or s.strike <= 0 or s.seconds_left <= 0:
        raise ValueError("invalid spot/strike/horizon")
    if s.sigma_per_sqrt_second <= 0 or s.max_staleness_seconds < 0:
        raise ValueError("invalid volatility/staleness")
    age = s.decision_timestamp - s.spot_timestamp
    if age < 0 or age > s.max_staleness_seconds:
        raise ValueError("future or stale spot snapshot")
    scale = s.sigma_per_sqrt_second * math.sqrt(s.seconds_left)
    z = (math.log(s.spot / s.strike) - 0.5 * scale * scale) / scale
    return 0.5 * math.erfc(-z / math.sqrt(2.0))

def assess_underdog(s: Snapshot, side: str, executable_price: float,
                    total_cost_per_contract: float = 0.0,
                    probability_error_margin: float = 0.0) -> dict:
    """Conservative gross directional EV gate, before execution/fill modeling.

    Positive result is NOT permission to trade. The margin must come from
    independent chronological calibration, not tuned to test outcomes.
    """
    if side not in ("UP", "DOWN"):
        raise ValueError("side must be UP or DOWN")
    if not all(math.isfinite(x) for x in
               (executable_price, total_cost_per_contract, probability_error_margin)):
        raise ValueError("nonfinite price/cost/margin")
    if not 0 < executable_price < 1 or total_cost_per_contract < 0:
        raise ValueError("invalid price or cost")
    if not 0 <= probability_error_margin <= 1:
        raise ValueError("invalid probability margin")
    p_up = estimate_up(s)
    p = p_up if side == "UP" else 1.0 - p_up
    conservative_p = max(0.0, p - probability_error_margin)
    ev = conservative_p - executable_price - total_cost_per_contract
    return {"probability": p, "conservative_probability": conservative_p,
            "net_ev_before_fill_risk": ev, "candidate_only": ev > 0,
            "live_eligible": False, "calibrated": False}

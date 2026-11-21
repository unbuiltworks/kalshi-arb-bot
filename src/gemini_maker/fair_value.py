"""Fair value for Gemini 5m/15m up/down contracts under 60s-RFR settlement.

Settlement fact (from contract metadata): outcome is decided by
GRR-KAIKO_RFR_<ASSET>USD_60S — a 60-second rolling reference rate — vs the
strike captured at window open. So the settlement print is the AVERAGE of the
underlying over the final 60 seconds of the window, not the last tick.

Model (deliberately simple, fully testable):
- Underlying treated as a driftless random walk with volatility sigma
  (dollars per sqrt-second), estimated by the caller from recent spot trades.
- Let T = expiry, t = now, tau = T - t, w = 60s averaging window.
- tau >= w (before the averaging window):
    E[settle] = spot_now
    Var[settle] = sigma^2 * ((tau - w) + w/3)
  (variance of the future average of a Brownian path: full variance up to the
  window start, then the 1/3 factor inside the averaged stretch).
- tau < w (inside the averaging window; part of the average is REALIZED):
    E[settle] = (realized_sum + spot_now * tau) / w
      where realized_sum = integral of observed spot over [T-w, t]
      (caller supplies realized_mean over that stretch and its coverage).
    Var[settle] = sigma^2 * tau^3 / (3 * w^2)
  This is the RFR edge in one line: late in the window most of the settlement
  average is already known, whatever the book still thinks.

p_yes = Phi((E[settle] - strike) / sqrt(Var)), clamped to [0.001, 0.999].

All constants/approximations here are v1 and frozen only after the lag study
validates them against realized settlements.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

RFR_WINDOW_S = 60.0


@dataclass(frozen=True)
class FairValue:
    p_yes: float
    expected_settle: float
    stdev: float
    regime: str          # "pre_window" | "in_window"
    realized_share: float  # fraction of the settlement average already fixed


def realized_vol_per_sqrt_s(trail: Sequence[Tuple[float, float]],
                            min_points: int = 8) -> Optional[float]:
    """Sigma in dollars/sqrt(second) from a recent (t_seconds, price) trail."""
    if len(trail) < min_points:
        return None
    num = 0.0
    den = 0.0
    for (t0, p0), (t1, p1) in zip(trail, list(trail)[1:]):
        dt = t1 - t0
        if dt <= 0:
            continue
        num += (p1 - p0) ** 2
        den += dt
    if den <= 0 or num <= 0:
        return None
    return math.sqrt(num / den)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def fair_value(
    *,
    strike: float,
    spot_now: float,
    seconds_to_expiry: float,
    sigma: float,
    realized_mean: Optional[float] = None,
    realized_coverage_s: float = 0.0,
) -> Optional[FairValue]:
    """p_yes for 'settle >= strike'.

    realized_mean / realized_coverage_s: mean of observed spot over the part
    of [T-60, now] already elapsed, and how many seconds it covers. Required
    accuracy only matters once inside the averaging window.
    """
    if strike <= 0 or spot_now <= 0 or sigma is None or sigma <= 0:
        return None
    tau = max(0.0, seconds_to_expiry)
    w = RFR_WINDOW_S
    if tau >= w:
        expected = spot_now
        var = sigma * sigma * ((tau - w) + w / 3.0)
        regime = "pre_window"
        realized_share = 0.0
    else:
        elapsed = w - tau
        if realized_mean is None or realized_coverage_s < 0.5 * elapsed:
            # Not enough realized-window data: fall back to spot for the
            # realized stretch (degrades gracefully, flagged by share=0).
            realized_mean = spot_now
        expected = (realized_mean * elapsed + spot_now * tau) / w
        var = sigma * sigma * (tau ** 3) / (3.0 * w * w)
        regime = "in_window"
        realized_share = elapsed / w
    if var <= 0:
        p = 1.0 if expected >= strike else 0.0
        return FairValue(min(max(p, 0.001), 0.999), expected, 0.0, regime,
                         realized_share)
    stdev = math.sqrt(var)
    p = _phi((expected - strike) / stdev)
    return FairValue(min(max(p, 0.001), 0.999), expected, stdev, regime,
                     realized_share)

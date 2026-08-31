"""Causal volume/velocity gate for Gemini 5-minute L5 strategies.

This module deliberately uses only information observable *before* an entry decision.
It never selects a window from its final volume.  The gate is strategy-agnostic: it
only decides whether new exposure may be opened.  Rescue, cancel, reconciliation and
flatten logic must always remain active after an entry, even if this gate later closes.

The first deployment is read-only shadow telemetry.  Thresholds are intentionally
environment-configurable so retained windows can be replayed before any live use.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal as D
from typing import Deque

ZERO = D("0")
WINDOW_SECONDS = 300.0


@dataclass(frozen=True)
class VolumeGateConfig:
    warmup_seconds: float = 20.0
    velocity_window_seconds: float = 30.0
    min_velocity_span_seconds: float = 5.0
    enter_volume_usd: D = D("150")
    enter_projected_volume_usd: D = D("500")
    enter_velocity_usd_per_second: D = D("1.50")
    exit_projected_volume_usd: D = D("300")
    exit_velocity_usd_per_second: D = D("0.50")
    confirmations_to_open: int = 2
    confirmations_to_close: int = 3

    def validate(self) -> None:
        if not 0 <= self.warmup_seconds < WINDOW_SECONDS:
            raise ValueError("warmup_seconds must be inside the 5-minute window")
        if not 1 <= self.velocity_window_seconds <= WINDOW_SECONDS:
            raise ValueError("velocity_window_seconds must be in [1, 300]")
        if not 0 < self.min_velocity_span_seconds <= self.velocity_window_seconds:
            raise ValueError("min_velocity_span_seconds must be positive and <= velocity window")
        for name, value in (
            ("enter_volume_usd", self.enter_volume_usd),
            ("enter_projected_volume_usd", self.enter_projected_volume_usd),
            ("enter_velocity_usd_per_second", self.enter_velocity_usd_per_second),
            ("exit_projected_volume_usd", self.exit_projected_volume_usd),
            ("exit_velocity_usd_per_second", self.exit_velocity_usd_per_second),
        ):
            if value < ZERO:
                raise ValueError(f"{name} must be non-negative")
        if self.confirmations_to_open < 1 or self.confirmations_to_close < 1:
            raise ValueError("confirmation counts must be positive")


@dataclass(frozen=True)
class VolumeGateDecision:
    elapsed_seconds: float
    seconds_to_expiry: float
    volume_usd: D
    velocity_usd_per_second: D
    projected_final_volume_usd: D
    tier: str
    gate_open: bool
    allow_new_entries: bool
    reason: str
    open_confirmations: int
    close_confirmations: int

    def as_dict(self) -> dict[str, object]:
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "seconds_to_expiry": round(self.seconds_to_expiry, 3),
            "volume_usd": str(self.volume_usd),
            "velocity_usd_per_second": str(self.velocity_usd_per_second),
            "projected_final_volume_usd": str(self.projected_final_volume_usd),
            "tier": self.tier,
            "gate_open": self.gate_open,
            "allow_new_entries": self.allow_new_entries,
            "reason": self.reason,
            "open_confirmations": self.open_confirmations,
            "close_confirmations": self.close_confirmations,
        }


class FiveMinuteVolumeGate:
    """Stateful, hysteretic gate using only cumulative volume observed so far."""

    def __init__(self, config: VolumeGateConfig | None = None) -> None:
        self.config = config or VolumeGateConfig()
        self.config.validate()
        self.samples: Deque[tuple[float, D]] = deque()
        self.gate_open = False
        self._open_confirmations = 0
        self._close_confirmations = 0

    def reset(self) -> None:
        self.samples.clear()
        self.gate_open = False
        self._open_confirmations = 0
        self._close_confirmations = 0

    @staticmethod
    def _clean_volume(value: D) -> D:
        value = D(str(value))
        if value < ZERO:
            raise ValueError("volume_usd cannot be negative")
        return value

    def _append(self, elapsed_seconds: float, volume_usd: D) -> None:
        if self.samples and elapsed_seconds < self.samples[-1][0]:
            raise ValueError("elapsed_seconds must be monotonic within a window")
        # Venue counters should be cumulative.  Fail closed on a reset/decrease instead
        # of manufacturing negative velocity from a likely window identity change.
        if self.samples and volume_usd < self.samples[-1][1]:
            raise ValueError("volume_usd decreased; reset gate for the new window")
        self.samples.append((elapsed_seconds, volume_usd))
        cutoff = elapsed_seconds - self.config.velocity_window_seconds
        while len(self.samples) > 2 and self.samples[1][0] <= cutoff:
            self.samples.popleft()

    def _velocity(self) -> D:
        if len(self.samples) < 2:
            return ZERO
        newest_t, newest_v = self.samples[-1]
        oldest_t, oldest_v = self.samples[0]
        span = newest_t - oldest_t
        if span < self.config.min_velocity_span_seconds:
            return ZERO
        delta = max(ZERO, newest_v - oldest_v)
        return delta / D(str(span))

    @staticmethod
    def _tier(volume_usd: D, projected: D) -> str:
        effective = max(volume_usd, projected)
        if effective >= D("1000"):
            return "VERY_HOT"
        if effective >= D("500"):
            return "HOT"
        if effective >= D("250"):
            return "WARM"
        return "COLD"

    def observe(self, *, volume_usd: D, seconds_to_expiry: float) -> VolumeGateDecision:
        volume = self._clean_volume(volume_usd)
        seconds_left = max(0.0, min(WINDOW_SECONDS, float(seconds_to_expiry)))
        elapsed = WINDOW_SECONDS - seconds_left
        self._append(elapsed, volume)
        velocity = self._velocity()
        projected = volume + velocity * D(str(seconds_left))
        projected = max(volume, projected)
        tier = self._tier(volume, projected)

        if elapsed < self.config.warmup_seconds:
            self._open_confirmations = 0
            reason = "WARMUP"
        else:
            entry_signal = bool(
                volume >= self.config.enter_volume_usd
                or (
                    projected >= self.config.enter_projected_volume_usd
                    and velocity >= self.config.enter_velocity_usd_per_second
                )
            )
            if not self.gate_open:
                if entry_signal:
                    self._open_confirmations += 1
                else:
                    self._open_confirmations = 0
                if self._open_confirmations >= self.config.confirmations_to_open:
                    self.gate_open = True
                    self._close_confirmations = 0
                    reason = "FLOW_CONFIRMED_OPEN"
                else:
                    reason = "FLOW_CONFIRMING" if entry_signal else "FLOW_BELOW_ENTRY_GATE"
            else:
                exit_signal = bool(
                    projected < self.config.exit_projected_volume_usd
                    and velocity < self.config.exit_velocity_usd_per_second
                )
                if exit_signal:
                    self._close_confirmations += 1
                else:
                    self._close_confirmations = 0
                if self._close_confirmations >= self.config.confirmations_to_close:
                    self.gate_open = False
                    self._open_confirmations = 0
                    reason = "FLOW_DECAYED_CLOSE"
                else:
                    reason = "FLOW_OPEN"

        return VolumeGateDecision(
            elapsed_seconds=elapsed,
            seconds_to_expiry=seconds_left,
            volume_usd=volume,
            velocity_usd_per_second=velocity,
            projected_final_volume_usd=projected,
            tier=tier,
            gate_open=self.gate_open,
            allow_new_entries=self.gate_open,
            reason=reason,
            open_confirmations=self._open_confirmations,
            close_confirmations=self._close_confirmations,
        )


def threshold_grid(*, volume_usd: D, projected_final_volume_usd: D) -> dict[str, bool]:
    """Non-binding shadow labels used to compare candidate final-volume bands."""
    volume = max(ZERO, D(str(volume_usd)))
    projected = max(volume, D(str(projected_final_volume_usd)))
    effective = max(volume, projected)
    return {
        "projected_250_plus": effective >= D("250"),
        "projected_500_plus": effective >= D("500"),
        "projected_1000_plus": effective >= D("1000"),
        "observed_250_plus": volume >= D("250"),
        "observed_500_plus": volume >= D("500"),
        "observed_1000_plus": volume >= D("1000"),
    }

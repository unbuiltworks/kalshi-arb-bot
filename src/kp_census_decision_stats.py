from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional


EDGE_THRESHOLDS_USD = (0.01, 0.02, 0.03)
REALISTIC_CAPTURE_MIN_MS = 80.0


@dataclass(frozen=True)
class CensusObservation:
    """One rule-certified, after-fee executable K<->P observation.

    This module is intentionally transport- and order-entry-free. Callers must
    only pass observations that already passed settlement/rule certification.
    """

    semantic_key: str
    observed_ms: float
    net_edge_usd: float
    q1_depth: float
    quote_skew_ms: float
    process_latency_ms: float
    source_quality: str


@dataclass
class _SignalState:
    opened_ms: float
    last_seen_ms: float
    max_edge_usd: float
    min_depth: float
    max_skew_ms: float
    source_quality: str


@dataclass(frozen=True)
class ClosedSignal:
    semantic_key: str
    lifetime_ms: float
    max_edge_usd: float
    min_depth: float
    max_skew_ms: float
    source_quality: str


@dataclass
class CensusDecisionStats:
    """Accumulates explicit decision metrics for the pre-committed edge census."""

    capture_min_ms: float = REALISTIC_CAPTURE_MIN_MS
    observations: int = 0
    unique_semantic_keys: set[str] = field(default_factory=set)
    bins: dict[str, int] = field(
        default_factory=lambda: {">=1c": 0, ">=2c": 0, ">=3c": 0}
    )
    capture_bins: dict[str, int] = field(
        default_factory=lambda: {">=1c": 0, ">=2c": 0, ">=3c": 0}
    )
    depth_sum: float = 0.0
    skew_sum_ms: float = 0.0
    process_latency_sum_ms: float = 0.0
    max_depth: float = 0.0
    max_skew_ms: float = 0.0
    open_signals: dict[str, _SignalState] = field(default_factory=dict)
    closed_signals: list[ClosedSignal] = field(default_factory=list)

    @staticmethod
    def _bin_labels(edge_usd: float) -> Iterable[str]:
        if edge_usd >= 0.01:
            yield ">=1c"
        if edge_usd >= 0.02:
            yield ">=2c"
        if edge_usd >= 0.03:
            yield ">=3c"

    def observe(self, obs: CensusObservation) -> None:
        if not obs.semantic_key:
            raise ValueError("semantic_key is required")
        if obs.q1_depth < 0 or obs.quote_skew_ms < 0 or obs.process_latency_ms < 0:
            raise ValueError("depth/skew/latency must be non-negative")

        self.observations += 1
        self.unique_semantic_keys.add(obs.semantic_key)
        self.depth_sum += obs.q1_depth
        self.skew_sum_ms += obs.quote_skew_ms
        self.process_latency_sum_ms += obs.process_latency_ms
        self.max_depth = max(self.max_depth, obs.q1_depth)
        self.max_skew_ms = max(self.max_skew_ms, obs.quote_skew_ms)

        labels = tuple(self._bin_labels(obs.net_edge_usd))
        for label in labels:
            self.bins[label] += 1

        state = self.open_signals.get(obs.semantic_key)
        if labels:
            if state is None:
                state = _SignalState(
                    opened_ms=obs.observed_ms,
                    last_seen_ms=obs.observed_ms,
                    max_edge_usd=obs.net_edge_usd,
                    min_depth=obs.q1_depth,
                    max_skew_ms=obs.quote_skew_ms,
                    source_quality=obs.source_quality,
                )
                self.open_signals[obs.semantic_key] = state
            else:
                state.last_seen_ms = obs.observed_ms
                state.max_edge_usd = max(state.max_edge_usd, obs.net_edge_usd)
                state.min_depth = min(state.min_depth, obs.q1_depth)
                state.max_skew_ms = max(state.max_skew_ms, obs.quote_skew_ms)

            # A realistic capture is credited only after the same semantic
            # opportunity survives for the fixed minimum time. REST sampled
            # sources remain labeled as such; this does not claim WS lifetime.
            survived_ms = obs.observed_ms - state.opened_ms
            if survived_ms >= self.capture_min_ms and obs.q1_depth >= 1.0:
                for label in labels:
                    self.capture_bins[label] += 1
        elif state is not None:
            self._close(obs.semantic_key, obs.observed_ms)

    def close_missing(self, observed_ms: float, present_keys: Iterable[str]) -> None:
        present = set(present_keys)
        for key in tuple(self.open_signals):
            if key not in present:
                self._close(key, observed_ms)

    def _close(self, semantic_key: str, closed_ms: float) -> None:
        state = self.open_signals.pop(semantic_key, None)
        if state is None:
            return
        self.closed_signals.append(
            ClosedSignal(
                semantic_key=semantic_key,
                lifetime_ms=max(0.0, closed_ms - state.opened_ms),
                max_edge_usd=state.max_edge_usd,
                min_depth=state.min_depth,
                max_skew_ms=state.max_skew_ms,
                source_quality=state.source_quality,
            )
        )

    def summary(self) -> dict[str, object]:
        n = self.observations
        lifetimes = [signal.lifetime_ms for signal in self.closed_signals]
        return {
            "observations": n,
            "unique_semantic_keys": len(self.unique_semantic_keys),
            "edge_bins": dict(self.bins),
            "realistic_capture_min_ms": self.capture_min_ms,
            "realistic_capture_bins": dict(self.capture_bins),
            "avg_q1_depth": (self.depth_sum / n) if n else 0.0,
            "max_q1_depth": self.max_depth,
            "avg_quote_skew_ms": (self.skew_sum_ms / n) if n else 0.0,
            "max_quote_skew_ms": self.max_skew_ms,
            "avg_process_latency_ms": (self.process_latency_sum_ms / n) if n else 0.0,
            "closed_signal_count": len(lifetimes),
            "avg_closed_lifetime_ms": (sum(lifetimes) / len(lifetimes)) if lifetimes else 0.0,
            "max_closed_lifetime_ms": max(lifetimes, default=0.0),
            "open_signal_count": len(self.open_signals),
            "source_quality_counts": self._source_quality_counts(),
        }

    def _source_quality_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for signal in self.closed_signals:
            counts[signal.source_quality] = counts.get(signal.source_quality, 0) + 1
        for signal in self.open_signals.values():
            counts[signal.source_quality] = counts.get(signal.source_quality, 0) + 1
        return counts

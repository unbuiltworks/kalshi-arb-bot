"""Pure sportsbook-to-prediction-market fair-value utilities.

No networking or execution. Converts bookmaker odds to de-vigged probabilities
and compares them with a binary prediction-market quote.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class FairValueResult:
    fair_probability: float
    market_price: float
    raw_edge: float
    threshold: float

    @property
    def passes(self) -> bool:
        return self.raw_edge >= self.threshold


def decimal_odds_to_implied(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError("decimal_odds must be > 1")
    return 1.0 / decimal_odds


def proportional_devig(decimal_odds: Iterable[float]) -> list[float]:
    implied = [decimal_odds_to_implied(x) for x in decimal_odds]
    total = sum(implied)
    if total <= 0:
        raise ValueError("invalid implied probability total")
    return [p / total for p in implied]


def compare_binary_price(
    fair_probability: float,
    market_price: float,
    *,
    min_edge: float = 0.03,
) -> FairValueResult:
    if not 0.0 <= fair_probability <= 1.0:
        raise ValueError("fair_probability must be between 0 and 1")
    if not 0.0 <= market_price <= 1.0:
        raise ValueError("market_price must be between 0 and 1")
    if min_edge < 0:
        raise ValueError("min_edge cannot be negative")
    return FairValueResult(
        fair_probability=fair_probability,
        market_price=market_price,
        raw_edge=fair_probability - market_price,
        threshold=min_edge,
    )

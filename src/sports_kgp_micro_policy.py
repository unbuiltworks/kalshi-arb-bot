"""Fail-closed micro-live policy for sports prediction-market arbitrage.

This policy is deliberately venue-agnostic across Kalshi, Gemini and Polymarket,
but excludes sportsbooks. It does not place orders. Callers must still provide an
execution-approved contract rule fingerprint and use venue-specific transports.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

D = Decimal
PREDICTION_VENUES = frozenset({"kalshi", "gemini", "polymarket"})
SUPPORTED_PAIRS = frozenset({
    frozenset({"kalshi", "gemini"}),
    frozenset({"kalshi", "polymarket"}),
    frozenset({"gemini", "polymarket"}),
})


@dataclass(frozen=True)
class MicroLivePolicy:
    max_contracts_per_leg: int = 1
    max_combined_capital_usd: Decimal = D("2.00")
    min_after_fee_profit_per_contract: Decimal = D("0.03")
    max_quote_skew_ms: int = 50
    max_book_age_ms: int = 100
    min_persistence_ms: int = 10
    max_attempts_per_arm: int = 1


@dataclass(frozen=True)
class MicroCandidate:
    venues: tuple[str, str]
    quantity: Decimal
    combined_capital_usd: Decimal
    after_fee_profit_per_contract: Decimal
    quote_skew_ms: int
    max_book_age_ms: int
    persistence_ms: int
    approved_for_execution: bool
    rule_fingerprint_sha256: str


def _valid_sha256(value: str) -> bool:
    raw = str(value or "").strip().lower()
    return len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw)


def validate_micro_candidate(candidate: MicroCandidate, policy: MicroLivePolicy | None = None) -> tuple[bool, str]:
    p = policy or MicroLivePolicy()
    venues = tuple(str(v).strip().lower() for v in candidate.venues)
    pair = frozenset(venues)
    if len(venues) != 2 or len(pair) != 2:
        return False, "exactly_two_distinct_venues_required"
    if not pair.issubset(PREDICTION_VENUES) or pair not in SUPPORTED_PAIRS:
        return False, "prediction_market_pair_only"
    if not candidate.approved_for_execution:
        return False, "contract_not_execution_approved"
    if not _valid_sha256(candidate.rule_fingerprint_sha256):
        return False, "invalid_rule_fingerprint"
    if candidate.quantity <= 0 or candidate.quantity > D(p.max_contracts_per_leg):
        return False, "quantity_exceeds_micro_cap"
    if candidate.combined_capital_usd <= 0 or candidate.combined_capital_usd > p.max_combined_capital_usd:
        return False, "capital_exceeds_micro_cap"
    if candidate.after_fee_profit_per_contract < p.min_after_fee_profit_per_contract:
        return False, "after_fee_profit_below_floor"
    if int(candidate.quote_skew_ms) > p.max_quote_skew_ms:
        return False, "quote_skew_too_large"
    if int(candidate.max_book_age_ms) > p.max_book_age_ms:
        return False, "book_too_old"
    if int(candidate.persistence_ms) < p.min_persistence_ms:
        return False, "opportunity_not_persistent"
    return True, "qualified"


def prediction_pair_only(venues: Iterable[str]) -> bool:
    normalized = tuple(str(v).strip().lower() for v in venues)
    return len(normalized) == 2 and frozenset(normalized) in SUPPORTED_PAIRS

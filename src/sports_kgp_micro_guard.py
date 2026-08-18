"""Fail-closed planning guard for tiny automated sports prediction-market arbs.

This module contains no network clients and cannot submit orders. It converts an
already-priced cross-venue lock into an exact one-contract execution plan only when
all dangerous invariants are revalidated. The approval file is intentionally empty
until a real pair is fully certified.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal
import time
from typing import Any, Mapping

from src.three_venue_pricing_core import PricedQuote
from src.universal_prediction_quant import LockOpportunity

D = Decimal
ONE = D("1")
EXECUTABLE_CONFIDENCE = "FULL_RULES_AUDITED_EXECUTABLE"
ALLOWED_VENUES = frozenset({"kalshi", "gemini", "polymarket"})
ALLOWED_SOURCE_SIDES = frozenset({"YES", "NO", "CONTRACT"})
EXECUTABLE_DEPTH_SOURCE_BY_VENUE = {
    "gemini": frozenset({"gemini_depth10_100ms_final"}),
    "kalshi": frozenset({"kalshi_public_orderbook_depth1_final"}),
    "polymarket": frozenset({"polymarket_public_book_final"}),
}
DEFAULT_MIN_UNIT_PROFIT = D("0.03")
DEFAULT_MAX_QUOTE_SKEW_MS = 50
DEFAULT_MAX_BOOK_AGE_MS = 100
DEFAULT_MAX_CAPITAL = D("10.00")
INITIAL_QUANTITY = ONE
ECON_TOLERANCE = D("0.000001")


def _base_market_id(market_id: str) -> str:
    return str(market_id).split("#", 1)[0]


@dataclass(frozen=True)
class RuleApproval:
    settlement_key: str
    venue_a: str
    market_a: str
    source_side_a: str
    venue_b: str
    market_b: str
    source_side_b: str
    rule_fingerprint_sha256: str
    approved_for_execution: bool

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "RuleApproval":
        return cls(
            settlement_key=str(row.get("settlement_key") or ""),
            venue_a=str(row.get("venue_a") or "").lower(),
            market_a=str(row.get("market_a") or ""),
            source_side_a=str(row.get("source_side_a") or "").upper(),
            venue_b=str(row.get("venue_b") or "").lower(),
            market_b=str(row.get("market_b") or ""),
            source_side_b=str(row.get("source_side_b") or "").upper(),
            rule_fingerprint_sha256=str(row.get("rule_fingerprint_sha256") or "").lower(),
            approved_for_execution=bool(row.get("approved_for_execution")),
        )

    @property
    def venues(self) -> frozenset[str]:
        return frozenset((self.venue_a, self.venue_b))

    def market_for(self, venue: str) -> str:
        if venue == self.venue_a:
            return self.market_a
        if venue == self.venue_b:
            return self.market_b
        raise KeyError(venue)

    def source_side_for(self, venue: str) -> str:
        if venue == self.venue_a:
            return self.source_side_a
        if venue == self.venue_b:
            return self.source_side_b
        raise KeyError(venue)

    def validate(self) -> None:
        if not self.approved_for_execution:
            raise PermissionError("rule approval is not execution-approved")
        if not self.settlement_key:
            raise ValueError("rule approval is missing settlement_key")
        if self.venue_a == self.venue_b or not self.venues.issubset(ALLOWED_VENUES):
            raise ValueError("approval must contain two distinct K/G/P venues")
        if not self.market_a or not self.market_b:
            raise ValueError("rule approval is missing exact market IDs")
        if self.source_side_a not in ALLOWED_SOURCE_SIDES or self.source_side_b not in ALLOWED_SOURCE_SIDES:
            raise ValueError("invalid native source-side orientation")
        fp = self.rule_fingerprint_sha256
        if len(fp) != 64 or any(ch not in "0123456789abcdef" for ch in fp):
            raise ValueError("rule approval requires a SHA-256 fingerprint")


@dataclass(frozen=True)
class MicroLeg:
    venue: str
    market_id: str
    normalized_outcome: str
    native_outcome: str
    raw_outcome_price: Decimal
    source_side: str
    received_ms: int

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["raw_outcome_price"] = str(self.raw_outcome_price)
        return row


@dataclass(frozen=True)
class MicroExecutionPlan:
    settlement_key: str
    quantity: Decimal
    unit_cost: Decimal
    locked_profit: Decimal
    quote_skew_ms: int
    created_ms: int
    yes_leg: MicroLeg
    no_leg: MicroLeg
    rule_fingerprint_sha256: str

    @property
    def capital(self) -> Decimal:
        return self.unit_cost * self.quantity

    def as_dict(self) -> dict[str, Any]:
        return {
            "settlement_key": self.settlement_key,
            "quantity": str(self.quantity),
            "unit_cost": str(self.unit_cost),
            "capital": str(self.capital),
            "locked_profit": str(self.locked_profit),
            "quote_skew_ms": self.quote_skew_ms,
            "created_ms": self.created_ms,
            "yes_leg": self.yes_leg.as_dict(),
            "no_leg": self.no_leg.as_dict(),
            "rule_fingerprint_sha256": self.rule_fingerprint_sha256,
        }


def _native_outcome(source_side: str, normalized: str) -> str:
    if normalized not in {"YES", "NO"}:
        raise ValueError("normalized outcome must be YES or NO")
    if source_side in {"YES", "CONTRACT"}:
        return normalized
    if source_side == "NO":
        return "NO" if normalized == "YES" else "YES"
    raise ValueError("unsupported source-side orientation")


def _leg(row: PricedQuote, normalized: str) -> MicroLeg:
    raw = row.raw_yes_ask if normalized == "YES" else row.raw_no_ask
    return MicroLeg(
        venue=row.quote.venue,
        market_id=_base_market_id(row.quote.market_id),
        normalized_outcome=normalized,
        native_outcome=_native_outcome(row.source_side, normalized),
        raw_outcome_price=D(raw),
        source_side=row.source_side,
        received_ms=int(row.quote.received_ms),
    )


def build_micro_plan(
    opportunity: LockOpportunity,
    priced_quotes: list[PricedQuote],
    approval: RuleApproval,
    *,
    now_ms: int | None = None,
    min_unit_profit: Decimal = DEFAULT_MIN_UNIT_PROFIT,
    max_quote_skew_ms: int = DEFAULT_MAX_QUOTE_SKEW_MS,
    max_book_age_ms: int = DEFAULT_MAX_BOOK_AGE_MS,
    max_capital: Decimal = DEFAULT_MAX_CAPITAL,
) -> MicroExecutionPlan:
    """Revalidate and freeze an exact q=1 K/G/P execution plan."""
    approval.validate()
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)

    pair = frozenset((str(opportunity.yes_venue), str(opportunity.no_venue)))
    if len(pair) != 2 or not pair.issubset(ALLOWED_VENUES):
        raise ValueError("automatic sports execution is prediction-market-only")
    if pair != approval.venues:
        raise PermissionError("opportunity venues do not match the audited approval")
    if opportunity.settlement_key != approval.settlement_key:
        raise PermissionError("opportunity settlement key is not approved")
    if str(opportunity.settlement_confidence) != EXECUTABLE_CONFIDENCE:
        raise PermissionError("opportunity is not fully rule-audited executable")
    if D(str(opportunity.max_quantity)) < INITIAL_QUANTITY:
        raise ValueError("less than one contract of displayed depth")
    if D(str(opportunity.unit_profit)) < D(min_unit_profit):
        raise ValueError("after-fee edge below micro-live threshold")
    if int(opportunity.quote_skew_ms) > int(max_quote_skew_ms):
        raise ValueError("cross-venue quote skew above micro-live threshold")

    current = {
        row.quote.venue: row
        for row in priced_quotes
        if row.quote.venue in pair and row.quote.settlement_key == opportunity.settlement_key
    }
    if set(current) != set(pair):
        raise ValueError("exact current priced quote missing for one execution venue")

    for venue, row in current.items():
        if str(row.quote.settlement_confidence) != EXECUTABLE_CONFIDENCE:
            raise PermissionError(f"{venue} current quote is not execution-audited")
        allowed_sources = EXECUTABLE_DEPTH_SOURCE_BY_VENUE.get(venue, frozenset())
        if str(row.depth_source) not in allowed_sources:
            raise PermissionError(f"{venue} quote is not from an approved final execution book")
        age = now_ms - int(row.quote.received_ms)
        if age < 0 or age > int(max_book_age_ms):
            raise ValueError(f"{venue} book is stale at execution gate")
        if _base_market_id(row.quote.market_id) != approval.market_for(venue):
            raise PermissionError(f"{venue} market ID changed since rule approval")
        if row.source_side != approval.source_side_for(venue):
            raise PermissionError(f"{venue} native orientation changed since rule approval")

    yes_row = current[str(opportunity.yes_venue)]
    no_row = current[str(opportunity.no_venue)]
    yes_leg = _leg(yes_row, "YES")
    no_leg = _leg(no_row, "NO")

    yes_all_in = D(yes_row.quote.yes_cost)
    no_all_in = D(no_row.quote.no_cost)
    unit_cost = yes_all_in + no_all_in
    locked = ONE - unit_cost
    if abs(locked - D(str(opportunity.unit_profit))) > ECON_TOLERANCE:
        raise ValueError("current outbound economics changed after candidate selection")
    if locked < D(min_unit_profit):
        raise ValueError("recomputed after-fee edge below micro-live threshold")
    if unit_cost * INITIAL_QUANTITY > D(max_capital):
        raise ValueError("one-contract pair exceeds micro-live capital ceiling")

    return MicroExecutionPlan(
        settlement_key=opportunity.settlement_key,
        quantity=INITIAL_QUANTITY,
        unit_cost=unit_cost,
        locked_profit=locked,
        quote_skew_ms=int(opportunity.quote_skew_ms),
        created_ms=now_ms,
        yes_leg=yes_leg,
        no_leg=no_leg,
        rule_fingerprint_sha256=approval.rule_fingerprint_sha256,
    )

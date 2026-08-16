"""Read-only, venue-agnostic prediction-market opportunity optimizer.

This module deliberately contains no venue clients and no order-entry code. Feed
adapters normalize executable all-in YES/NO costs into ``MarketQuote`` objects;
the optimizer then finds and ranks complementary cross-venue locks.

The key design rule is that semantic matching happens *before* this module. Quotes
may only share a ``settlement_key`` after the relevant market rules have been
audited to the confidence level recorded on each quote.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from typing import Iterable

D = Decimal


CONFIDENCE_RANK = {
    "CANDIDATE_ONLY": 0,
    "NEEDS_RULE_AUDIT": 1,
    "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE": 2,
    "FULL_RULES_AUDITED_SHADOW": 3,
    "FULL_RULES_AUDITED_EXECUTABLE": 4,
}


@dataclass(frozen=True)
class MarketQuote:
    venue: str
    market_id: str
    settlement_key: str
    category: str
    yes_cost: Decimal
    no_cost: Decimal
    yes_depth: Decimal
    no_depth: Decimal
    received_ms: int
    settlement_confidence: str
    expected_settlement_minutes: int | None = None

    def __post_init__(self) -> None:
        for name, value in (("yes_cost", self.yes_cost), ("no_cost", self.no_cost)):
            if value < 0 or value > 1:
                raise ValueError(f"{name} must be within [0, 1]")
        if self.yes_depth < 0 or self.no_depth < 0:
            raise ValueError("depth cannot be negative")
        if self.settlement_confidence not in CONFIDENCE_RANK:
            raise ValueError(f"unknown settlement confidence: {self.settlement_confidence}")


@dataclass(frozen=True)
class LockOpportunity:
    settlement_key: str
    category: str
    yes_venue: str
    yes_market_id: str
    no_venue: str
    no_market_id: str
    unit_cost: Decimal
    unit_profit: Decimal
    max_quantity: Decimal
    max_capital: Decimal
    max_locked_profit: Decimal
    quote_skew_ms: int
    settlement_confidence: str
    expected_settlement_minutes: int | None
    capital_velocity_score: Decimal

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "settlement_key": self.settlement_key,
            "category": self.category,
            "yes_venue": self.yes_venue,
            "yes_market_id": self.yes_market_id,
            "no_venue": self.no_venue,
            "no_market_id": self.no_market_id,
            "unit_cost": str(self.unit_cost),
            "unit_profit": str(self.unit_profit),
            "max_quantity": str(self.max_quantity),
            "max_capital": str(self.max_capital),
            "max_locked_profit": str(self.max_locked_profit),
            "quote_skew_ms": self.quote_skew_ms,
            "settlement_confidence": self.settlement_confidence,
            "expected_settlement_minutes": self.expected_settlement_minutes,
            "capital_velocity_score": str(self.capital_velocity_score),
        }


def _weaker_confidence(a: str, b: str) -> str:
    return a if CONFIDENCE_RANK[a] <= CONFIDENCE_RANK[b] else b


def _settlement_minutes(a: MarketQuote, b: MarketQuote) -> int | None:
    vals = [x for x in (a.expected_settlement_minutes, b.expected_settlement_minutes) if x is not None]
    return max(vals) if vals else None


def _velocity(profit: Decimal, capital: Decimal, settlement_minutes: int | None) -> Decimal:
    if capital <= 0 or profit <= 0:
        return D("0")
    minutes = D(str(max(1, settlement_minutes or 1440)))
    # Expected locked dollars produced per capital-dollar per day. This is a
    # prioritization metric, not a forecast of repeatability.
    return (profit / capital) * (D("1440") / minutes)


def evaluate_direction(yes: MarketQuote, no: MarketQuote) -> LockOpportunity | None:
    if yes.venue == no.venue:
        return None
    if yes.settlement_key != no.settlement_key:
        return None
    if yes.category != no.category:
        return None
    qty = min(yes.yes_depth, no.no_depth)
    if qty <= 0:
        return None
    unit_cost = yes.yes_cost + no.no_cost
    unit_profit = D("1") - unit_cost
    if unit_profit <= 0:
        return None
    capital = unit_cost * qty
    profit = unit_profit * qty
    minutes = _settlement_minutes(yes, no)
    confidence = _weaker_confidence(yes.settlement_confidence, no.settlement_confidence)
    return LockOpportunity(
        settlement_key=yes.settlement_key,
        category=yes.category,
        yes_venue=yes.venue,
        yes_market_id=yes.market_id,
        no_venue=no.venue,
        no_market_id=no.market_id,
        unit_cost=unit_cost,
        unit_profit=unit_profit,
        max_quantity=qty,
        max_capital=capital,
        max_locked_profit=profit,
        quote_skew_ms=abs(yes.received_ms - no.received_ms),
        settlement_confidence=confidence,
        expected_settlement_minutes=minutes,
        capital_velocity_score=_velocity(profit, capital, minutes),
    )


def find_cross_venue_locks(
    quotes: Iterable[MarketQuote],
    *,
    min_unit_profit: Decimal = D("0"),
    max_quote_skew_ms: int | None = None,
    min_confidence: str = "NEEDS_RULE_AUDIT",
) -> list[LockOpportunity]:
    rows = list(quotes)
    threshold = CONFIDENCE_RANK[min_confidence]
    out: list[LockOpportunity] = []
    for a, b in combinations(rows, 2):
        if a.settlement_key != b.settlement_key:
            continue
        for yes, no in ((a, b), (b, a)):
            opp = evaluate_direction(yes, no)
            if opp is None or opp.unit_profit < min_unit_profit:
                continue
            if max_quote_skew_ms is not None and opp.quote_skew_ms > max_quote_skew_ms:
                continue
            if CONFIDENCE_RANK[opp.settlement_confidence] < threshold:
                continue
            out.append(opp)
    out.sort(
        key=lambda x: (
            x.capital_velocity_score,
            x.max_locked_profit,
            x.unit_profit,
        ),
        reverse=True,
    )
    return out


def best_lock_by_contract(quotes: Iterable[MarketQuote], **kwargs: object) -> list[LockOpportunity]:
    best: dict[str, LockOpportunity] = {}
    for opp in find_cross_venue_locks(quotes, **kwargs):
        best.setdefault(opp.settlement_key, opp)
    return list(best.values())


def summarize(opportunities: Iterable[LockOpportunity]) -> dict[str, object]:
    rows = list(opportunities)
    by_pair: dict[str, dict[str, Decimal | int]] = {}
    by_category: dict[str, dict[str, Decimal | int]] = {}
    for row in rows:
        pair = f"{row.yes_venue}->{row.no_venue}"
        for bucket, key in ((by_pair, pair), (by_category, row.category)):
            item = bucket.setdefault(key, {"count": 0, "max_locked_profit": D("0"), "max_capital": D("0")})
            item["count"] = int(item["count"]) + 1
            item["max_locked_profit"] = D(str(item["max_locked_profit"])) + row.max_locked_profit
            item["max_capital"] = D(str(item["max_capital"])) + row.max_capital
    def clean(src: dict[str, dict[str, Decimal | int]]) -> dict[str, dict[str, str | int]]:
        return {
            key: {
                "count": int(value["count"]),
                "max_locked_profit": str(value["max_locked_profit"]),
                "max_capital": str(value["max_capital"]),
            }
            for key, value in sorted(src.items())
        }
    return {
        "opportunity_count": len(rows),
        "venue_pairs": clean(by_pair),
        "categories": clean(by_category),
        "read_only": True,
        "order_entry": False,
    }

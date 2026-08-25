"""Pure q=1 cross-threshold middle evaluation for funded sports venues.

This module performs no network I/O and no order entry. It compares two
semantically-normalized contracts in the same physical event/market family at
different thresholds and computes the payout in every normal-event region.
A candidate is only a mathematical lock when the *minimum* q=1 profit is
non-negative; the middle payout is never used to hide an outside-region loss.

Physical event date is an explicit matching dimension. Team names plus a similar
scheduled-start timestamp are not enough: feeds can carry stale or mis-normalized
starts for a different game in the same series. Missing or different event dates
therefore fail closed before any candidate can be produced.

Rule certification is deliberately separate from normal-event math. An observed
price pair may be useful shadow telemetry while remaining ineligible for live
execution because postponement, overtime/extra-innings, stat correction, push,
or official-source semantics are not certified. Sportsbooks remain manual-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

D = Decimal
ONE = D("1")
TWO = D("2")
FUNDED_VENUES = frozenset({"kalshi", "gemini", "polymarket", "sportsbook:fanduel"})
THRESHOLD_FAMILIES = frozenset({"spread", "game_total", "team_total", "player_prop", "team_prop", "game_prop"})
SUPPORTED_COMPARATORS = frozenset({"GT", "GE", "LT", "LE"})


@dataclass(frozen=True)
class ThresholdObservedQuote:
    venue: str
    market_id: str
    settlement_key: str
    market_family: str
    scope: str
    subject_type: str
    subject: str
    stat: str
    comparator: str
    line: Decimal
    yes_cost: Decimal
    no_cost: Decimal
    yes_depth: Decimal
    no_depth: Decimal
    scheduled_start_ms: int
    received_ms: int
    physical_event_date: str = ""
    source_side: str = "CONTRACT"
    rules_complete: bool = False
    rule_fingerprint: str = "INCOMPLETE"

    @property
    def family_key(self) -> tuple[str, ...]:
        parts = self.settlement_key.split("|")
        if len(parts) < 10:
            raise ValueError(f"bad settlement key: {self.settlement_key}")
        # sport | league | participants | family | scope | subject_type |
        # subject | stat | comparator | line. Physical date is deliberately
        # separate because legacy settlement keys do not encode it.
        return tuple(parts[:8])

    @property
    def underlying_market_id(self) -> str:
        if self.venue in {"gemini", "polymarket", "kalshi"} and "#" in self.market_id:
            return self.market_id.rsplit("#", 1)[0]
        return self.market_id

    def actual_action(self, semantic_action: str) -> str:
        action = semantic_action.upper()
        if action not in {"YES", "NO"}:
            raise ValueError(f"unsupported semantic action: {semantic_action}")
        if self.source_side == "NO":
            return "NO" if action == "YES" else "YES"
        return action

    def action_cost(self, semantic_action: str) -> Decimal:
        return self.yes_cost if semantic_action.upper() == "YES" else self.no_cost

    def action_depth(self, semantic_action: str) -> Decimal:
        return self.yes_depth if semantic_action.upper() == "YES" else self.no_depth


@dataclass(frozen=True)
class MiddleLeg:
    venue: str
    market_id: str
    actual_action: str
    semantic_action: str
    comparator: str
    line: Decimal
    all_in_cost: Decimal
    q1_depth: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "market_id": self.market_id,
            "actual_action": self.actual_action,
            "semantic_action": self.semantic_action,
            "comparator": self.comparator,
            "line": str(self.line),
            "all_in_cost": str(self.all_in_cost),
            "q1_depth": str(self.q1_depth),
        }


@dataclass(frozen=True)
class MiddleCandidate:
    family_key: tuple[str, ...]
    physical_event_date: str
    comparator: str
    low_line: Decimal
    high_line: Decimal
    middle_region: str
    low_leg: MiddleLeg
    high_leg: MiddleLeg
    total_cost: Decimal
    floor_payout: Decimal
    middle_payout: Decimal
    floor_profit: Decimal
    middle_profit: Decimal
    quote_skew_ms: int
    start_skew_ms: int
    classification: str
    rules_match: bool
    rule_certified: bool
    autoexecution_eligible: bool
    blockers: tuple[str, ...]

    @property
    def venue_pair(self) -> tuple[str, str]:
        return tuple(sorted((self.low_leg.venue, self.high_leg.venue)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_key": "|".join(self.family_key),
            "physical_event_date": self.physical_event_date,
            "venue_pair": list(self.venue_pair),
            "comparator": self.comparator,
            "low_line": str(self.low_line),
            "high_line": str(self.high_line),
            "middle_region": self.middle_region,
            "low_leg": self.low_leg.as_dict(),
            "high_leg": self.high_leg.as_dict(),
            "total_cost": str(self.total_cost),
            "floor_payout": str(self.floor_payout),
            "middle_payout": str(self.middle_payout),
            "floor_profit": str(self.floor_profit),
            "middle_profit": str(self.middle_profit),
            "quote_skew_ms": self.quote_skew_ms,
            "start_skew_ms": self.start_skew_ms,
            "classification": self.classification,
            "rules_match": self.rules_match,
            "rule_certified": self.rule_certified,
            "autoexecution_eligible": self.autoexecution_eligible,
            "blockers": list(self.blockers),
        }


def _middle_actions(comparator: str) -> tuple[str, str, str]:
    if comparator == "GT":
        return "YES", "NO", "(low, high]"
    if comparator == "GE":
        return "YES", "NO", "[low, high)"
    if comparator == "LT":
        return "NO", "YES", "[low, high)"
    if comparator == "LE":
        return "NO", "YES", "(low, high]"
    raise ValueError(f"unsupported comparator: {comparator}")


def _classification(floor_profit: Decimal, middle_profit: Decimal, min_floor_profit: Decimal) -> str | None:
    if floor_profit >= min_floor_profit:
        return "SHADOW_LOCKED_MIDDLE_3C_PLUS"
    if floor_profit >= D("0"):
        return "SHADOW_POSITIVE_LOCK_BELOW_3C"
    if middle_profit > D("0"):
        return "SPORTS_MIDDLE_EV_SHAPE_NOT_ARBITRAGE"
    return None


def _leg(row: ThresholdObservedQuote, semantic_action: str) -> MiddleLeg:
    return MiddleLeg(
        venue=row.venue,
        market_id=row.underlying_market_id,
        actual_action=row.actual_action(semantic_action),
        semantic_action=semantic_action,
        comparator=row.comparator,
        line=row.line,
        all_in_cost=row.action_cost(semantic_action),
        q1_depth=row.action_depth(semantic_action),
    )


def _candidate_key(candidate: MiddleCandidate) -> tuple[Any, ...]:
    legs = tuple(sorted((
        (candidate.low_leg.venue, candidate.low_leg.market_id, candidate.low_leg.actual_action),
        (candidate.high_leg.venue, candidate.high_leg.market_id, candidate.high_leg.actual_action),
    )))
    return (legs, candidate.physical_event_date, candidate.comparator, candidate.low_line, candidate.high_line)


def find_threshold_middles(
    quotes: Iterable[ThresholdObservedQuote],
    *,
    min_floor_profit: Decimal = D("0.03"),
    max_start_skew_ms: int = 5 * 60 * 1000,
    max_quote_skew_ms: int = 70_000,
) -> list[MiddleCandidate]:
    """Evaluate funded-venue q=1 cross-threshold pairs conservatively."""
    rows = [q for q in quotes if q.venue in FUNDED_VENUES]
    best: dict[tuple[Any, ...], MiddleCandidate] = {}
    for i, left in enumerate(rows):
        if left.market_family not in THRESHOLD_FAMILIES or left.comparator not in SUPPORTED_COMPARATORS:
            continue
        if not left.physical_event_date:
            continue
        for right in rows[i + 1:]:
            if right.market_family not in THRESHOLD_FAMILIES:
                continue
            if left.venue == right.venue:
                continue
            if not right.physical_event_date or left.physical_event_date != right.physical_event_date:
                continue
            if left.family_key != right.family_key or left.comparator != right.comparator:
                continue
            if left.line == right.line:
                continue
            start_skew = abs(left.scheduled_start_ms - right.scheduled_start_ms)
            quote_skew = abs(left.received_ms - right.received_ms)
            if start_skew > max_start_skew_ms or quote_skew > max_quote_skew_ms:
                continue
            low, high = (left, right) if left.line < right.line else (right, left)
            low_action, high_action, middle_region = _middle_actions(low.comparator)
            low_leg, high_leg = _leg(low, low_action), _leg(high, high_action)
            if low_leg.q1_depth < ONE or high_leg.q1_depth < ONE:
                continue
            total_cost = low_leg.all_in_cost + high_leg.all_in_cost
            floor_profit = ONE - total_cost
            middle_profit = TWO - total_cost
            classification = _classification(floor_profit, middle_profit, min_floor_profit)
            if classification is None:
                continue

            rules_match = (
                low.rules_complete
                and high.rules_complete
                and low.rule_fingerprint != "INCOMPLETE"
                and low.rule_fingerprint == high.rule_fingerprint
            )
            blockers: list[str] = []
            if not rules_match:
                blockers.append("SETTLEMENT_RULE_AUDIT_REQUIRED")
            sportsbook_present = low.venue.startswith("sportsbook:") or high.venue.startswith("sportsbook:")
            if sportsbook_present:
                blockers.append("SPORTSBOOK_MANUAL_ONLY")
            rule_certified = rules_match
            autoexecution_eligible = classification == "SHADOW_LOCKED_MIDDLE_3C_PLUS" and rules_match and not sportsbook_present
            candidate = MiddleCandidate(
                family_key=low.family_key,
                physical_event_date=low.physical_event_date,
                comparator=low.comparator,
                low_line=low.line,
                high_line=high.line,
                middle_region=middle_region,
                low_leg=low_leg,
                high_leg=high_leg,
                total_cost=total_cost,
                floor_payout=ONE,
                middle_payout=TWO,
                floor_profit=floor_profit,
                middle_profit=middle_profit,
                quote_skew_ms=quote_skew,
                start_skew_ms=start_skew,
                classification=classification,
                rules_match=rules_match,
                rule_certified=rule_certified,
                autoexecution_eligible=autoexecution_eligible,
                blockers=tuple(blockers),
            )
            key = _candidate_key(candidate)
            previous = best.get(key)
            if previous is None or candidate.floor_profit > previous.floor_profit:
                best[key] = candidate

    order = {
        "SHADOW_LOCKED_MIDDLE_3C_PLUS": 0,
        "SHADOW_POSITIVE_LOCK_BELOW_3C": 1,
        "SPORTS_MIDDLE_EV_SHAPE_NOT_ARBITRAGE": 2,
    }
    return sorted(
        best.values(),
        key=lambda x: (order.get(x.classification, 99), -x.floor_profit, -x.middle_profit, x.venue_pair, x.low_line),
    )

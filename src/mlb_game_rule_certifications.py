"""Fail-closed MLB full-game settlement-rule certification.

This module performs no network I/O and contains no order-entry code. It records
human-audited venue semantics separately from normal-event identity, then decides
whether a venue pair is safe to label execution-equivalent.

A pair is FULL_RULES_AUDITED_EXECUTABLE only when every settlement dimension is
identical. Normal-game similarity with different postponement/cancellation tails
is CONDITIONAL and must remain alert-blocked.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from src.sports_pushover_alerts import ACTIONABLE_CONFIDENCE

AUDIT_DATE = "2026-08-18"
SHADOW_CONFIDENCE = "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
SUPPORTED_FAMILY = "moneyline"
NORMAL_WINNER = "named team wins official game"
EXTRA_INNINGS = "included"


class CertificationStatus(str, Enum):
    CERTIFIED = "CERTIFIED"
    CONDITIONAL = "CONDITIONAL"
    REJECTED = "REJECTED"
    UNAUDITED = "UNAUDITED"


@dataclass(frozen=True)
class VenueRuleProfile:
    venue: str
    family: str
    official_source: str
    normal_winner: str
    extra_innings: str
    not_started: str
    interrupted: str
    cancellation: str
    tie_push: str
    source_ref: str
    complete: bool = True

    def settlement_tuple(self) -> tuple[str, ...]:
        return (
            self.official_source.strip().lower(),
            self.normal_winner.strip().lower(),
            self.extra_innings.strip().lower(),
            self.not_started.strip().lower(),
            self.interrupted.strip().lower(),
            self.cancellation.strip().lower(),
            self.tie_push.strip().lower(),
        )


@dataclass(frozen=True)
class PairAssessment:
    left_venue: str
    right_venue: str
    family: str
    status: CertificationStatus
    exact_settlement_equivalent: bool
    blockers: tuple[str, ...]
    audit_date: str = AUDIT_DATE
    read_only: bool = True
    order_entry: bool = False

    @property
    def execution_confidence(self) -> str:
        return ACTIONABLE_CONFIDENCE if self.status is CertificationStatus.CERTIFIED else SHADOW_CONFIDENCE

    @property
    def execution_eligible(self) -> bool:
        return self.status is CertificationStatus.CERTIFIED and self.exact_settlement_equivalent

    def as_dict(self) -> dict[str, Any]:
        return {
            "left_venue": self.left_venue,
            "right_venue": self.right_venue,
            "family": self.family,
            "status": self.status.value,
            "exact_settlement_equivalent": self.exact_settlement_equivalent,
            "blockers": list(self.blockers),
            "audit_date": self.audit_date,
            "execution_confidence": self.execution_confidence,
            "execution_eligible": self.execution_eligible,
            "read_only": self.read_only,
            "order_entry": self.order_entry,
        }


def _p(venue: str, *, official_source: str, not_started: str, interrupted: str,
       cancellation: str, tie_push: str, source_ref: str) -> VenueRuleProfile:
    return VenueRuleProfile(
        venue=venue,
        family=SUPPORTED_FAMILY,
        official_source=official_source,
        normal_winner=NORMAL_WINNER,
        extra_innings=EXTRA_INNINGS,
        not_started=not_started,
        interrupted=interrupted,
        cancellation=cancellation,
        tie_push=tie_push,
        source_ref=source_ref,
    )


# Profiles are semantic summaries, not copied rulebook prose. Source references
# identify the official pages reviewed on AUDIT_DATE. Ordinary completed-game
# semantics are intentionally normalized; exceptional lifecycle differences remain.
PROFILES: dict[tuple[str, str], VenueRuleProfile] = {
    ("draftkings", SUPPORTED_FAMILY): _p(
        "draftkings",
        official_source="official governing body",
        not_started="void if game does not start on scheduled local date",
        interrupted="regular-season moneyline requires official and concluded within two calendar days or void",
        cancellation="void",
        tie_push="push",
        source_ref="https://sportsbook.draftkings.com/help/sport-rules/baseball",
    ),
    ("fanduel", SUPPORTED_FAMILY): _p(
        "fanduel",
        official_source="official governing body",
        not_started="void when scheduled-game start requirement is not met",
        interrupted="sportsbook suspension/official-game rules can settle or void before full completion",
        cancellation="void under sportsbook cancellation branch",
        tie_push="push or void under sportsbook rules",
        source_ref="fanduel official sportsbook house rules",
    ),
    ("kalshi", SUPPORTED_FAMILY): _p(
        "kalshi",
        official_source="espn fox sports and governing league",
        not_started="postponed or delayed market remains open for rescheduled game within two days",
        interrupted="market remains open for qualifying rescheduled completion within two days",
        cancellation="fair-price resolution if cancelled or rescheduled over two days away",
        tie_push="exceptional no-winner branch handled by market rules; no sportsbook push",
        source_ref="https://kalshi.com/markets/kxmlbgame/professional-baseball-game",
    ),
    ("polymarket", SUPPORTED_FAMILY): _p(
        "polymarket",
        official_source="official final statistics from governing body or event organizer",
        not_started="postponed market remains open until game is completed",
        interrupted="remains open until completed",
        cancellation="50-50 if cancelled entirely with no makeup game",
        tie_push="50-50 on tie or no winning side under listed mlb rules",
        source_ref="https://polymarket.com/sports/mlb",
    ),
    ("gemini", SUPPORTED_FAMILY): _p(
        "gemini",
        official_source="mlb espn fox sports or broadcast partner",
        not_started="unresolved expiration value can enter market outcome review",
        interrupted="official result or market outcome review when expiration value cannot be determined",
        cancellation="market outcome review process rather than sportsbook void",
        tie_push="market outcome review/no sportsbook push contract",
        source_ref="gemini prediction market mlb contract rules",
    ),
}


def canonical_venue(value: str) -> str:
    out = str(value).strip().lower()
    if out.startswith("sportsbook:"):
        out = out.split(":", 1)[1]
    if out in {"polymarket_us", "polymarket-us"}:
        return "polymarket"
    return out


def family_from_settlement_key(key: str) -> str | None:
    parts = str(key).split("|")
    return parts[3].strip().lower() if len(parts) > 3 and parts[3].strip() else None


def assess_pair(left_venue: str, right_venue: str, family: str) -> PairAssessment:
    left_name = canonical_venue(left_venue)
    right_name = canonical_venue(right_venue)
    family = str(family).strip().lower()
    left = PROFILES.get((left_name, family))
    right = PROFILES.get((right_name, family))
    if left is None or right is None or not left.complete or not right.complete:
        return PairAssessment(
            left_name, right_name, family, CertificationStatus.UNAUDITED, False,
            ("missing_complete_venue_family_rule_profile",),
        )

    dimensions = (
        ("official_source", left.official_source, right.official_source),
        ("normal_winner", left.normal_winner, right.normal_winner),
        ("extra_innings", left.extra_innings, right.extra_innings),
        ("not_started", left.not_started, right.not_started),
        ("interrupted", left.interrupted, right.interrupted),
        ("cancellation", left.cancellation, right.cancellation),
        ("tie_push", left.tie_push, right.tie_push),
    )
    blockers = tuple(name for name, a, b in dimensions if a.strip().lower() != b.strip().lower())
    exact = not blockers
    if exact:
        status = CertificationStatus.CERTIFIED
    elif (
        left.normal_winner.strip().lower() == right.normal_winner.strip().lower()
        and left.extra_innings.strip().lower() == right.extra_innings.strip().lower()
    ):
        status = CertificationStatus.CONDITIONAL
    else:
        status = CertificationStatus.REJECTED
    return PairAssessment(left_name, right_name, family, status, exact, blockers)


def assess_lock(lock: Any) -> PairAssessment:
    family = family_from_settlement_key(getattr(lock, "settlement_key", "")) or "unknown"
    return assess_pair(str(getattr(lock, "yes_venue", "")), str(getattr(lock, "no_venue", "")), family)


def apply_certification(lock: Any) -> tuple[Any, PairAssessment]:
    """Upgrade only a registry-certified lock; all real current pairs stay closed."""
    assessment = assess_lock(lock)
    if not assessment.execution_eligible:
        return lock, assessment
    return replace(lock, settlement_confidence=ACTIONABLE_CONFIDENCE), assessment


def audit_matrix() -> dict[str, Any]:
    sportsbooks = ("fanduel", "draftkings")
    predictions = ("kalshi", "polymarket", "gemini")
    rows = [assess_pair(book, venue, SUPPORTED_FAMILY).as_dict() for book in sportsbooks for venue in predictions]
    return {
        "audit_date": AUDIT_DATE,
        "family": SUPPORTED_FAMILY,
        "pairs": rows,
        "certified_pairs": sum(1 for row in rows if row["status"] == CertificationStatus.CERTIFIED.value),
        "read_only": True,
        "order_entry": False,
    }

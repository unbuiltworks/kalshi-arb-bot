"""Fail-closed MLB full-game settlement-rule certification.

This module performs no network I/O and contains no order-entry code. It records
human-audited venue semantics separately from normal-event identity, then decides
whether a venue pair is safe to label execution-equivalent.

A pair is FULL_RULES_AUDITED_EXECUTABLE only when every settlement dimension is
identical. Normal-game similarity with different completion, postponement,
cancellation, or resolution-source tails is CONDITIONAL and remains alert-blocked.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from itertools import combinations
from typing import Any, Iterable

from src.sports_pushover_alerts import ACTIONABLE_CONFIDENCE

AUDIT_DATE = "2026-08-18"
SHADOW_CONFIDENCE = "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
SUPPORTED_FAMILIES = ("moneyline", "spread", "game_total")
PREDICTION_VENUES = ("kalshi", "polymarket", "gemini")
SPORTSBOOK_VENUES = ("fanduel", "draftkings")
EXTRA_INNINGS = "included"

NORMAL_OUTCOME = {
    "moneyline": "named_team_wins_specified_game",
    "spread": "named_team_margin_exceeds_line",
    "game_total": "combined_runs_compared_with_line",
}


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
    normal_outcome: str
    extra_innings: str
    minimum_completion: str
    not_started: str
    interrupted: str
    cancellation: str
    push_tie: str
    source_ref: str
    complete: bool = True

    def settlement_tuple(self) -> tuple[str, ...]:
        return (
            self.official_source.strip().lower(),
            self.normal_outcome.strip().lower(),
            self.extra_innings.strip().lower(),
            self.minimum_completion.strip().lower(),
            self.not_started.strip().lower(),
            self.interrupted.strip().lower(),
            self.cancellation.strip().lower(),
            self.push_tie.strip().lower(),
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


def _profile(
    venue: str,
    family: str,
    *,
    official_source: str,
    minimum_completion: str,
    not_started: str,
    interrupted: str,
    cancellation: str,
    push_tie: str,
    source_ref: str,
) -> VenueRuleProfile:
    return VenueRuleProfile(
        venue=venue,
        family=family,
        official_source=official_source,
        normal_outcome=NORMAL_OUTCOME[family],
        extra_innings=EXTRA_INNINGS,
        minimum_completion=minimum_completion,
        not_started=not_started,
        interrupted=interrupted,
        cancellation=cancellation,
        push_tie=push_tie,
        source_ref=source_ref,
    )


def _sportsbook_profiles(venue: str, source_ref: str, *, start_rule: str, interruption_rule: str) -> list[VenueRuleProfile]:
    source = "mlb_governing_body"
    return [
        _profile(
            venue,
            "moneyline",
            official_source=source,
            minimum_completion="official_after_5_or_4_5_if_home_leading",
            not_started=start_rule,
            interrupted=interruption_rule,
            cancellation="void_if_no_qualifying_completion",
            push_tie="push_or_void_on_tie",
            source_ref=source_ref,
        ),
        _profile(
            venue,
            "spread",
            official_source=source,
            minimum_completion="9_or_8_5_if_home_leading_unless_unconditionally_determined",
            not_started=start_rule,
            interrupted=interruption_rule,
            cancellation="void_if_no_qualifying_completion",
            push_tie="push_if_adjusted_margin_equals_line",
            source_ref=source_ref,
        ),
        _profile(
            venue,
            "game_total",
            official_source=source,
            minimum_completion="9_or_8_5_if_home_leading_unless_unconditionally_determined",
            not_started=start_rule,
            interrupted=interruption_rule,
            cancellation="void_if_no_qualifying_completion",
            push_tie="push_if_total_equals_line",
            source_ref=source_ref,
        ),
    ]


def _prediction_profiles(
    venue: str,
    source_ref: str,
    *,
    official_source: str,
    minimum_completion: str,
    not_started: str,
    interrupted: str,
    cancellation: str,
    push_moneyline: str,
    push_spread: str,
    push_total: str,
) -> list[VenueRuleProfile]:
    return [
        _profile(
            venue,
            "moneyline",
            official_source=official_source,
            minimum_completion=minimum_completion,
            not_started=not_started,
            interrupted=interrupted,
            cancellation=cancellation,
            push_tie=push_moneyline,
            source_ref=source_ref,
        ),
        _profile(
            venue,
            "spread",
            official_source=official_source,
            minimum_completion=minimum_completion,
            not_started=not_started,
            interrupted=interrupted,
            cancellation=cancellation,
            push_tie=push_spread,
            source_ref=source_ref,
        ),
        _profile(
            venue,
            "game_total",
            official_source=official_source,
            minimum_completion=minimum_completion,
            not_started=not_started,
            interrupted=interrupted,
            cancellation=cancellation,
            push_tie=push_total,
            source_ref=source_ref,
        ),
    ]


# Semantic summaries of official rules reviewed on AUDIT_DATE. We intentionally use
# normalized codes for ordinary outcomes so wording differences do not create fake
# blockers. Exceptional lifecycle and resolution-source differences stay explicit.
_PROFILE_ROWS: list[VenueRuleProfile] = []
_PROFILE_ROWS += _sportsbook_profiles(
    "draftkings",
    "https://sportsbook.draftkings.com/help/sport-rules/baseball",
    start_rule="void_if_not_started_on_scheduled_local_date",
    interruption_rule="void_if_required_innings_not_reached_unless_unconditionally_determined",
)
_PROFILE_ROWS += _sportsbook_profiles(
    "fanduel",
    "https://www.fanduel.com/fanduel-sportsbook-house-rules-ny",
    start_rule="void_if_not_started_on_scheduled_local_date",
    interruption_rule="resume_within_48h_or_void_unless_already_determined",
)
_PROFILE_ROWS += _prediction_profiles(
    "kalshi",
    "https://kalshi.com/markets/kxmlbgame/professional-baseball-game",
    official_source="mlb_espn_fox_and_governing_league",
    minimum_completion="specified_game_result_no_sportsbook_8_5_inning_void_rule",
    not_started="postponed_or_delayed_remains_open_for_rescheduled_game_within_2_days",
    interrupted="remains_open_for_rescheduled_completion_within_2_days",
    cancellation="fair_price_if_cancelled_or_rescheduled_more_than_2_days_away",
    push_moneyline="exceptional_no_winner_branch_uses_market_rules_not_sportsbook_push",
    push_spread="binary_contract_other_side_wins_when_margin_threshold_not_met",
    push_total="binary_contract_threshold_rule_no_sportsbook_push",
)
_PROFILE_ROWS += _prediction_profiles(
    "polymarket",
    "https://polymarket.com/sports/mlb",
    official_source="governing_body_final_stats_with_credible_reporting_fallback_after_24h",
    minimum_completion="market_remains_open_until_game_completed",
    not_started="postponed_market_remains_open_until_makeup_game_completed",
    interrupted="remains_open_until_game_completed",
    cancellation="50_50_if_cancelled_entirely_with_no_makeup_game",
    push_moneyline="50_50_if_tie_or_no_winning_side",
    push_spread="binary_opposite_side_when_margin_threshold_not_met; 50_50_if_game_cancelled",
    push_total="over_or_under_by_threshold; 50_50_if_game_cancelled",
)
_PROFILE_ROWS += _prediction_profiles(
    "gemini",
    "https://www.gemini.com/predictions/",
    official_source="mlb_then_espn_fox_or_broadcast_partner",
    minimum_completion="official_event_expiration_value_or_market_outcome_review",
    not_started="unresolved_expiration_value_can_enter_market_outcome_review",
    interrupted="official_result_or_market_outcome_review_if_expiration_value_unavailable",
    cancellation="market_outcome_review_process",
    push_moneyline="market_outcome_review_no_sportsbook_push_contract",
    push_spread="binary_contract_or_market_outcome_review",
    push_total="binary_contract_or_market_outcome_review",
)

PROFILES: dict[tuple[str, str], VenueRuleProfile] = {(row.venue, row.family): row for row in _PROFILE_ROWS}


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
            left_name,
            right_name,
            family,
            CertificationStatus.UNAUDITED,
            False,
            ("missing_complete_venue_family_rule_profile",),
        )

    dimensions = (
        ("official_source", left.official_source, right.official_source),
        ("normal_outcome", left.normal_outcome, right.normal_outcome),
        ("extra_innings", left.extra_innings, right.extra_innings),
        ("minimum_completion", left.minimum_completion, right.minimum_completion),
        ("not_started", left.not_started, right.not_started),
        ("interrupted", left.interrupted, right.interrupted),
        ("cancellation", left.cancellation, right.cancellation),
        ("push_tie", left.push_tie, right.push_tie),
    )
    blockers = tuple(name for name, a, b in dimensions if a.strip().lower() != b.strip().lower())
    exact = not blockers
    if exact:
        status = CertificationStatus.CERTIFIED
    elif (
        left.normal_outcome.strip().lower() == right.normal_outcome.strip().lower()
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
    """Upgrade only a registry-certified lock; every non-exact pair stays closed."""
    assessment = assess_lock(lock)
    if not assessment.execution_eligible:
        return lock, assessment
    return replace(lock, settlement_confidence=ACTIONABLE_CONFIDENCE), assessment


def _rows_for_pairs(pairs: Iterable[tuple[str, str]], family: str) -> list[dict[str, Any]]:
    return [assess_pair(left, right, family).as_dict() for left, right in pairs]


def audit_matrix(family: str | None = None) -> dict[str, Any]:
    families = (str(family).strip().lower(),) if family else SUPPORTED_FAMILIES
    sportsbook_pairs = tuple((book, venue) for book in SPORTSBOOK_VENUES for venue in PREDICTION_VENUES)
    direct_pairs = tuple(combinations(PREDICTION_VENUES, 2))

    family_rows: dict[str, Any] = {}
    all_rows: list[dict[str, Any]] = []
    for market_family in families:
        retail_rows = _rows_for_pairs(sportsbook_pairs, market_family)
        direct_rows = _rows_for_pairs(direct_pairs, market_family)
        rows = [*retail_rows, *direct_rows]
        all_rows.extend(rows)
        family_rows[market_family] = {
            "sportsbook_vs_prediction": retail_rows,
            "prediction_vs_prediction": direct_rows,
            "certified_pairs": sum(1 for row in rows if row["status"] == CertificationStatus.CERTIFIED.value),
            "conditional_pairs": sum(1 for row in rows if row["status"] == CertificationStatus.CONDITIONAL.value),
            "unaudited_pairs": sum(1 for row in rows if row["status"] == CertificationStatus.UNAUDITED.value),
        }

    return {
        "audit_date": AUDIT_DATE,
        "families": family_rows,
        "total_pairs": len(all_rows),
        "certified_pairs": sum(1 for row in all_rows if row["status"] == CertificationStatus.CERTIFIED.value),
        "conditional_pairs": sum(1 for row in all_rows if row["status"] == CertificationStatus.CONDITIONAL.value),
        "unaudited_pairs": sum(1 for row in all_rows if row["status"] == CertificationStatus.UNAUDITED.value),
        "read_only": True,
        "order_entry": False,
    }

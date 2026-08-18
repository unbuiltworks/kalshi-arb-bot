"""Fail-closed MLB team-total settlement certification.

Team totals are a distinct family from full-game totals: the ordinary proposition
is the named team's runs versus a line, while exceptional-game lifecycle rules can
still diverge by venue.  This module is read-only and exposes no order-entry path.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from itertools import combinations
from typing import Any

from src.sports_pushover_alerts import ACTIONABLE_CONFIDENCE

AUDIT_DATE = "2026-08-18"
FAMILY = "team_total"
SHADOW_CONFIDENCE = "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
SPORTSBOOK_VENUES = ("fanduel", "draftkings")
PREDICTION_VENUES = ("kalshi", "polymarket", "gemini")


class TeamTotalCertificationStatus(str, Enum):
    CERTIFIED = "CERTIFIED"
    CONDITIONAL = "CONDITIONAL"
    UNAUDITED = "UNAUDITED"


@dataclass(frozen=True)
class TeamTotalRuleProfile:
    venue: str
    official_source: str
    normal_outcome: str
    extra_innings: str
    minimum_completion: str
    not_started: str
    interrupted: str
    cancellation: str
    push_threshold: str
    source_ref: str
    complete: bool = True


@dataclass(frozen=True)
class TeamTotalPairAssessment:
    left_venue: str
    right_venue: str
    status: TeamTotalCertificationStatus
    blockers: tuple[str, ...]
    exact_settlement_equivalent: bool
    family: str = FAMILY
    audit_date: str = AUDIT_DATE
    read_only: bool = True
    order_entry: bool = False

    @property
    def execution_eligible(self) -> bool:
        return self.status is TeamTotalCertificationStatus.CERTIFIED and self.exact_settlement_equivalent

    @property
    def execution_confidence(self) -> str:
        return ACTIONABLE_CONFIDENCE if self.execution_eligible else SHADOW_CONFIDENCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "left_venue": self.left_venue,
            "right_venue": self.right_venue,
            "family": self.family,
            "status": self.status.value,
            "blockers": list(self.blockers),
            "exact_settlement_equivalent": self.exact_settlement_equivalent,
            "execution_eligible": self.execution_eligible,
            "execution_confidence": self.execution_confidence,
            "audit_date": self.audit_date,
            "read_only": self.read_only,
            "order_entry": self.order_entry,
        }


def canonical_venue(value: str) -> str:
    out = str(value or "").strip().lower()
    if out.startswith("sportsbook:"):
        out = out.split(":", 1)[1]
    if out in {"polymarket_us", "polymarket-us"}:
        return "polymarket"
    return out


NORMAL = "named_team_runs_compared_with_line"
EXTRAS = "included"
PROFILES: dict[str, TeamTotalRuleProfile] = {
    "draftkings": TeamTotalRuleProfile(
        venue="draftkings",
        official_source="mlb_governing_body",
        normal_outcome=NORMAL,
        extra_innings=EXTRAS,
        minimum_completion="9_or_8_5_if_home_leading_unless_unconditionally_determined",
        not_started="void_if_not_started_on_scheduled_local_date",
        interrupted="void_if_required_innings_not_reached_unless_unconditionally_determined",
        cancellation="void_if_no_qualifying_completion",
        push_threshold="push_if_team_total_equals_line",
        source_ref="https://sportsbook.draftkings.com/help/sport-rules/baseball",
    ),
    "fanduel": TeamTotalRuleProfile(
        venue="fanduel",
        official_source="mlb_governing_body",
        normal_outcome=NORMAL,
        extra_innings=EXTRAS,
        minimum_completion="sportsbook_full_game_team_total_completion_rule",
        not_started="void_if_scheduled_start_requirement_not_met",
        interrupted="sportsbook_suspension_rule_can_void_or_resume",
        cancellation="sportsbook_void_branch",
        push_threshold="push_if_team_total_equals_line",
        source_ref="FanDuel sportsbook house/baseball rules",
        complete=False,
    ),
    "kalshi": TeamTotalRuleProfile(
        venue="kalshi",
        official_source="mlb_espn_fox_and_governing_league",
        normal_outcome=NORMAL,
        extra_innings=EXTRAS,
        minimum_completion="specified_game_result_no_sportsbook_8_5_inning_void_rule",
        not_started="postponed_or_delayed_can_remain_open_for_rescheduled_game",
        interrupted="market_contract_rescheduled_completion_branch",
        cancellation="fair_price_or_contract_specific_exceptional_resolution",
        push_threshold="binary_threshold_contract_no_sportsbook_push",
        source_ref="Kalshi MLB game/team scoring contract rules",
        complete=False,
    ),
    "polymarket": TeamTotalRuleProfile(
        venue="polymarket",
        official_source="governing_body_final_stats_with_market_resolution_rules",
        normal_outcome=NORMAL,
        extra_innings=EXTRAS,
        minimum_completion="market_lifecycle_not_sportsbook_8_5_inning_rule",
        not_started="postponed_market_can_remain_open",
        interrupted="market_can_remain_open_until_resolution",
        cancellation="exceptional_market_resolution_can_differ_from_sportsbook_void",
        push_threshold="binary_threshold_market_resolution",
        source_ref="Polymarket sports market rules",
        complete=False,
    ),
    "gemini": TeamTotalRuleProfile(
        venue="gemini",
        official_source="prediction_contract_resolution_source",
        normal_outcome=NORMAL,
        extra_innings=EXTRAS,
        minimum_completion="event_expiration_value_or_market_outcome_review",
        not_started="unresolved_event_can_enter_market_outcome_review",
        interrupted="market_outcome_review_if_expiration_value_unavailable",
        cancellation="market_outcome_review_process",
        push_threshold="binary_team_total_contract_or_market_outcome_review",
        source_ref="https://developer.gemini.com/prediction-markets/tickers-sports",
        complete=False,
    ),
}


def assess_team_total_pair(left_venue: str, right_venue: str) -> TeamTotalPairAssessment:
    left_name = canonical_venue(left_venue)
    right_name = canonical_venue(right_venue)
    left = PROFILES.get(left_name)
    right = PROFILES.get(right_name)
    if left is None or right is None:
        return TeamTotalPairAssessment(
            left_name, right_name, TeamTotalCertificationStatus.UNAUDITED,
            ("missing_complete_venue_family_rule_profile",), False,
        )
    dimensions = (
        ("official_source", left.official_source, right.official_source),
        ("normal_outcome", left.normal_outcome, right.normal_outcome),
        ("extra_innings", left.extra_innings, right.extra_innings),
        ("minimum_completion", left.minimum_completion, right.minimum_completion),
        ("not_started", left.not_started, right.not_started),
        ("interrupted", left.interrupted, right.interrupted),
        ("cancellation", left.cancellation, right.cancellation),
        ("push_threshold", left.push_threshold, right.push_threshold),
    )
    blockers = [name for name, a, b in dimensions if a.strip().lower() != b.strip().lower()]
    if not left.complete or not right.complete:
        blockers.append("complete_lifecycle_fingerprint_required")
    blockers = list(dict.fromkeys(blockers))
    exact = not blockers and left.complete and right.complete
    status = (
        TeamTotalCertificationStatus.CERTIFIED
        if exact
        else TeamTotalCertificationStatus.CONDITIONAL
        if left.normal_outcome == right.normal_outcome
        else TeamTotalCertificationStatus.UNAUDITED
    )
    return TeamTotalPairAssessment(left_name, right_name, status, tuple(blockers), exact)


def apply_team_total_certification(lock: Any) -> tuple[Any, TeamTotalPairAssessment]:
    assessment = assess_team_total_pair(
        str(getattr(lock, "yes_venue", "")), str(getattr(lock, "no_venue", ""))
    )
    if not assessment.execution_eligible:
        return lock, assessment
    return replace(lock, settlement_confidence=ACTIONABLE_CONFIDENCE), assessment


def team_total_audit_matrix() -> dict[str, Any]:
    retail_pairs = tuple((book, venue) for book in SPORTSBOOK_VENUES for venue in PREDICTION_VENUES)
    direct_pairs = tuple(combinations(PREDICTION_VENUES, 2))
    retail = [assess_team_total_pair(a, b).as_dict() for a, b in retail_pairs]
    direct = [assess_team_total_pair(a, b).as_dict() for a, b in direct_pairs]
    rows = [*retail, *direct]
    return {
        "audit_date": AUDIT_DATE,
        "family": FAMILY,
        "sportsbook_vs_prediction": retail,
        "prediction_vs_prediction": direct,
        "total_pairs": len(rows),
        "certified_pairs": sum(r["status"] == TeamTotalCertificationStatus.CERTIFIED.value for r in rows),
        "conditional_pairs": sum(r["status"] == TeamTotalCertificationStatus.CONDITIONAL.value for r in rows),
        "unaudited_pairs": sum(r["status"] == TeamTotalCertificationStatus.UNAUDITED.value for r in rows),
        "read_only": True,
        "order_entry": False,
    }

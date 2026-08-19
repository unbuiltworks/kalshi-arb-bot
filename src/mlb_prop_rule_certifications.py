"""Fail-closed MLB player-prop settlement certification.

Identity equivalence is not enough.  This registry stores semantic fingerprints of
venue rules that have actually been reviewed.  A pair can only graduate when every
settlement dimension is complete and identical *and* the live participation gate
has been proven from official game state.

The 2026-08-19 audit materially improves the Kalshi/Polymarket US fingerprints but
deliberately does not certify the pair pregame: their exceptional-event clocks and
post-game correction semantics are not established as identical.  This module
performs no network I/O and contains no order-entry path.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from itertools import combinations
from typing import Any

from src.mlb_prop_rule_equivalence import (
    BATTER_FAMILIES,
    ParticipationProof,
    RuleEquivalence,
    SUPPORTED_FAMILIES,
    assess_rule_equivalence,
)
from src.sports_pushover_alerts import ACTIONABLE_CONFIDENCE

AUDIT_DATE = "2026-08-19"
SHADOW_CONFIDENCE = "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
PREDICTION_VENUES = ("kalshi", "polymarket", "gemini")
SPORTSBOOK_VENUES = ("fanduel", "draftkings")

KALSHI_SOURCE_REFS = {
    "baseball_player_hits": "https://assets.kalshi.com/contract_terms/MLBHITS.pdf",
    "baseball_player_hits_runs_rbis": "https://assets.kalshi.com/contract_terms/MLBHITSRUNSRBIS.pdf",
    "baseball_player_total_bases": "https://assets.kalshi.com/contract_terms/MLBTOTALBASES.pdf",
    "baseball_player_strikeouts": "https://assets.kalshi.com/contract_terms/MLBSTRIKEOUT.pdf",
}


class PropCertificationStatus(str, Enum):
    CERTIFIED = "CERTIFIED"
    PARTICIPATION_PENDING = "PARTICIPATION_PENDING"
    CONDITIONAL = "CONDITIONAL"
    UNAUDITED = "UNAUDITED"


@dataclass(frozen=True)
class PropVenueProfile:
    venue: str
    family: str
    participation_rule: str
    stat_definition: str
    official_source: str
    interruption: str
    cancellation: str
    stat_corrections: str
    threshold_push: str
    source_ref: str
    complete: bool = False


@dataclass(frozen=True)
class PropPairAssessment:
    left_venue: str
    right_venue: str
    family: str
    status: PropCertificationStatus
    participation_gate: str
    participation_proven: bool
    remaining_blockers: tuple[str, ...]
    exact_settlement_equivalent: bool
    audit_date: str = AUDIT_DATE
    read_only: bool = True
    order_entry: bool = False

    @property
    def execution_eligible(self) -> bool:
        return self.status is PropCertificationStatus.CERTIFIED and self.exact_settlement_equivalent

    @property
    def execution_confidence(self) -> str:
        return ACTIONABLE_CONFIDENCE if self.execution_eligible else SHADOW_CONFIDENCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "left_venue": self.left_venue,
            "right_venue": self.right_venue,
            "family": self.family,
            "status": self.status.value,
            "participation_gate": self.participation_gate,
            "participation_proven": self.participation_proven,
            "remaining_blockers": list(self.remaining_blockers),
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


def _family_normal_outcome(family: str) -> str:
    return {
        "baseball_player_hits": "official_hits_compared_with_threshold",
        "baseball_player_hits_runs_rbis": "official_hits_plus_runs_plus_rbis_compared_with_threshold",
        "baseball_player_total_bases": "official_total_bases_compared_with_threshold",
        "baseball_player_strikeouts": "official_pitcher_strikeouts_compared_with_threshold",
    }.get(family, "unknown")


def _profile(
    venue: str,
    family: str,
    *,
    participation_rule: str,
    official_source: str,
    interruption: str,
    cancellation: str,
    stat_corrections: str,
    threshold_push: str,
    source_ref: str,
    complete: bool,
) -> PropVenueProfile:
    return PropVenueProfile(
        venue=venue,
        family=family,
        participation_rule=participation_rule,
        stat_definition=_family_normal_outcome(family),
        official_source=official_source,
        interruption=interruption,
        cancellation=cancellation,
        stat_corrections=stat_corrections,
        threshold_push=threshold_push,
        source_ref=source_ref,
        complete=complete,
    )


def _kalshi_profile(family: str) -> PropVenueProfile:
    batter = family in BATTER_FAMILIES
    return _profile(
        "kalshi",
        family,
        participation_rule=(
            "lfmp_unless_starting_lineup_and_plate_appearance"
            if batter
            else "lfmp_unless_starting_pitcher_and_batter_faced"
        ),
        official_source=(
            "hierarchy_governing_league_official_scorer_mlbcom_espn_fox_official_broadcaster"
        ),
        interruption=(
            "extra_innings_count;preofficial_no_resume_within_48h_from_scheduled_start_lfmp;"
            "official_shortened_full_game_uses_official_result;resume_within_48h_uses_final_stats"
        ),
        cancellation=(
            "cancelled_postponed_suspended_preofficial_no_resume_within_48h_from_scheduled_start_lfmp"
        ),
        stat_corrections=(
            "official_stats_final_at_game_conclusion;post_conclusion_corrections_do_not_change_contract"
        ),
        threshold_push="binary_gte_integer_threshold",
        source_ref=KALSHI_SOURCE_REFS[family],
        complete=True,
    )


def _polymarket_profile(family: str) -> PropVenueProfile:
    batter = family in BATTER_FAMILIES
    if family == "baseball_player_total_bases":
        interruption = (
            "extra_innings_count;two_calendar_day_reschedule_window_else_lfmp;"
            "shortened_official_result_rule_not_explicit_in_sampled_live_instrument"
        )
    else:
        interruption = (
            "extra_innings_count;official_shortened_result_governs;"
            "two_calendar_day_reschedule_window_else_lfmp"
        )
    return _profile(
        "polymarket",
        family,
        participation_rule=(
            "lfmp_unless_starting_lineup_and_plate_appearance"
            if batter
            else "lfmp_unless_starting_pitcher"
        ),
        official_source=(
            "live_instrument_mlb;platform_hierarchy_governing_body_official_data_tertiary_sources"
        ),
        interruption=interruption,
        cancellation=(
            "delayed_postponed_suspended_not_rescheduled_to_date_within_two_calendar_days_lfmp"
        ),
        stat_corrections=(
            "post_game_stat_correction_window_not_explicit_in_live_instrument_or_sports_faq"
        ),
        threshold_push="binary_gte_integer_threshold",
        source_ref="live Polymarket US MLB prop instrument description + Polymarket Sports FAQ",
        # Fail closed: the correction tail is not completely fingerprinted and the
        # live two-calendar-day lifecycle is not proven identical to Kalshi's 48h clock.
        complete=False,
    )


_PROFILE_ROWS: list[PropVenueProfile] = []
for family in SUPPORTED_FAMILIES:
    _PROFILE_ROWS.append(_kalshi_profile(family))
    _PROFILE_ROWS.append(_polymarket_profile(family))
    _PROFILE_ROWS.append(_profile(
        "gemini",
        family,
        participation_rule="ticker_taxonomy_only_participation_rule_not_established",
        official_source="prediction_market_contract_source_rule_not_fully_fingerprinted",
        interruption="market_outcome_lifecycle_not_fully_fingerprinted",
        cancellation="market_outcome_lifecycle_not_fully_fingerprinted",
        stat_corrections="stat_correction_rule_not_fully_fingerprinted",
        threshold_push="ticker_support_established_settlement_rule_not_fully_fingerprinted",
        source_ref="https://developer.gemini.com/prediction-markets/tickers-sports",
        complete=False,
    ))
    _PROFILE_ROWS.append(_profile(
        "draftkings",
        family,
        participation_rule="pregame_prop_selection_must_start_and_participate_substitute_only_void",
        official_source="game_official_governing_body",
        interruption="single_game_player_prop_interruption_lifecycle_not_fully_matched_to_prediction_contracts",
        cancellation="game_not_started_scheduled_date_void",
        stat_corrections="governing_body_stat_determination",
        threshold_push="sportsbook_over_under_push_semantics_can_differ_from_binary_contract",
        source_ref="https://sportsbook.draftkings.com/help/sport-rules/baseball",
        complete=False,
    ))
    _PROFILE_ROWS.append(_profile(
        "fanduel",
        family,
        participation_rule="official_family_specific_participation_rule_requires_jurisdictional_audit",
        official_source="official_source_rule_requires_jurisdictional_audit",
        interruption="official_player_prop_lifecycle_requires_jurisdictional_audit",
        cancellation="official_player_prop_lifecycle_requires_jurisdictional_audit",
        stat_corrections="official_stat_correction_rule_requires_jurisdictional_audit",
        threshold_push="official_push_rule_requires_jurisdictional_audit",
        source_ref="FanDuel sportsbook house/baseball rules",
        complete=False,
    ))

PROFILES: dict[tuple[str, str], PropVenueProfile] = {
    (row.venue, row.family): row for row in _PROFILE_ROWS
}


def _participation_alignment(family: str, proof: ParticipationProof | None) -> tuple[str, bool]:
    result = assess_rule_equivalence(family, proof)
    return result.required_gate, result.status is RuleEquivalence.EQUIVALENT_AFTER_PARTICIPATION


def assess_prop_pair(
    left_venue: str,
    right_venue: str,
    family: str,
    proof: ParticipationProof | None = None,
) -> PropPairAssessment:
    """Assess one pair while removing only blockers proven resolved by game state."""
    left_name = canonical_venue(left_venue)
    right_name = canonical_venue(right_venue)
    family = str(family or "").strip().lower()
    gate, participation_proven = _participation_alignment(family, proof)

    left = PROFILES.get((left_name, family))
    right = PROFILES.get((right_name, family))
    if left is None or right is None:
        return PropPairAssessment(
            left_name,
            right_name,
            family,
            PropCertificationStatus.UNAUDITED,
            gate,
            participation_proven,
            ("missing_venue_family_rule_profile",),
            False,
        )

    dimensions = (
        ("stat_definition", left.stat_definition, right.stat_definition),
        ("official_source", left.official_source, right.official_source),
        ("interruption", left.interruption, right.interruption),
        ("cancellation", left.cancellation, right.cancellation),
        ("stat_corrections", left.stat_corrections, right.stat_corrections),
        ("threshold_push", left.threshold_push, right.threshold_push),
    )
    blockers = [name for name, a, b in dimensions if a.strip().lower() != b.strip().lower()]
    if not left.complete or not right.complete:
        blockers.append("complete_lifecycle_fingerprint_required")
    if not participation_proven:
        blockers.insert(0, "participation")

    blockers = list(dict.fromkeys(blockers))
    exact = participation_proven and not blockers and left.complete and right.complete
    if exact:
        status = PropCertificationStatus.CERTIFIED
    elif not participation_proven:
        status = PropCertificationStatus.PARTICIPATION_PENDING
    elif left.complete and right.complete:
        status = PropCertificationStatus.CONDITIONAL
    else:
        status = PropCertificationStatus.UNAUDITED

    return PropPairAssessment(
        left_name,
        right_name,
        family,
        status,
        gate,
        participation_proven,
        tuple(blockers),
        exact,
    )


def apply_prop_certification(
    lock: Any,
    *,
    family: str,
    proof: ParticipationProof | None = None,
) -> tuple[Any, PropPairAssessment]:
    assessment = assess_prop_pair(
        str(getattr(lock, "yes_venue", "")),
        str(getattr(lock, "no_venue", "")),
        family,
        proof,
    )
    if not assessment.execution_eligible:
        return lock, assessment
    return replace(lock, settlement_confidence=ACTIONABLE_CONFIDENCE), assessment


def prop_audit_matrix() -> dict[str, Any]:
    sportsbook_pairs = tuple((book, venue) for book in SPORTSBOOK_VENUES for venue in PREDICTION_VENUES)
    direct_pairs = tuple(combinations(PREDICTION_VENUES, 2))
    families: dict[str, Any] = {}
    all_rows: list[dict[str, Any]] = []
    for family in sorted(SUPPORTED_FAMILIES):
        retail = [assess_prop_pair(a, b, family).as_dict() for a, b in sportsbook_pairs]
        direct = [assess_prop_pair(a, b, family).as_dict() for a, b in direct_pairs]
        rows = [*retail, *direct]
        all_rows.extend(rows)
        families[family] = {
            "sportsbook_vs_prediction": retail,
            "prediction_vs_prediction": direct,
            "certified_pairs": sum(r["status"] == PropCertificationStatus.CERTIFIED.value for r in rows),
            "participation_pending_pairs": sum(r["status"] == PropCertificationStatus.PARTICIPATION_PENDING.value for r in rows),
            "unaudited_pairs": sum(r["status"] == PropCertificationStatus.UNAUDITED.value for r in rows),
        }
    return {
        "audit_date": AUDIT_DATE,
        "families": families,
        "total_pairs": len(all_rows),
        "certified_pairs": sum(r["status"] == PropCertificationStatus.CERTIFIED.value for r in all_rows),
        "participation_pending_pairs": sum(r["status"] == PropCertificationStatus.PARTICIPATION_PENDING.value for r in all_rows),
        "unaudited_pairs": sum(r["status"] == PropCertificationStatus.UNAUDITED.value for r in all_rows),
        "read_only": True,
        "order_entry": False,
    }

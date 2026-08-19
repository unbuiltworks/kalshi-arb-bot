"""Fail-closed MLB player-prop settlement certification.

The existing :mod:`mlb_prop_rule_equivalence` module answers one narrow question:
has the live participation branch that can differ between venues been resolved?
This module answers the larger execution question. Participation alignment is
necessary but not sufficient; a pair also needs an audited match for lifecycle,
official-stat authority, corrections, threshold/push treatment, and cancellation.

No real pair is upgraded merely because a player has participated. Unknown or
incompletely sourced lifecycle rules remain closed. This module performs no
network I/O and contains no order-entry path.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from itertools import combinations
from typing import Any

from src.mlb_prop_rule_equivalence import (
    BATTER_FAMILIES,
    PITCHER_FAMILIES,
    ParticipationProof,
    RuleEquivalence,
    SUPPORTED_FAMILIES,
    assess_rule_equivalence,
)
from src.sports_pushover_alerts import ACTIONABLE_CONFIDENCE

AUDIT_DATE = "2026-08-18"
SHADOW_CONFIDENCE = "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
PREDICTION_VENUES = ("kalshi", "polymarket", "gemini")
SPORTSBOOK_VENUES = ("fanduel", "draftkings")


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


# Semantic summaries of the venue rules reviewed on AUDIT_DATE. The important
# distinction is between evidence we have actually established and fields that are
# still missing a complete per-family legal fingerprint. ``complete=False`` is
# deliberate for every live venue until all exceptional-event and correction tails
# have been frozen from the exact current rule source. An incomplete profile can
# never certify a real-money pair.
_PROFILE_ROWS: list[PropVenueProfile] = []
for family in SUPPORTED_FAMILIES:
    _PROFILE_ROWS.append(_profile(
        "kalshi",
        family,
        participation_rule=(
            "batter_must_start_and_record_plate_appearance_else_fair_market_price"
            if family in BATTER_FAMILIES
            else "named_pitcher_must_start_and_record_pitch_else_fair_market_price"
        ),
        official_source="espn_fox_sports_and_governing_league",
        interruption="exact_family_full_terms_interruption_rule_not_fully_fingerprinted",
        cancellation="exact_family_full_terms_cancellation_rule_not_fully_fingerprinted",
        stat_corrections="exact_family_full_terms_stat_correction_rule_not_fully_fingerprinted",
        threshold_push="binary_integer_threshold_contract",
        source_ref="current Kalshi MLB player-prop market rules; exact family full rules still required",
        complete=False,
    ))
    _PROFILE_ROWS.append(_profile(
        "polymarket",
        family,
        participation_rule="lfmp_only_if_player_does_not_participate_at_all",
        official_source="governing_body_primary_then_official_secondary_then_major_tertiary_sources",
        interruption="official_result_if_threshold_reached_else_completion_before_expiration_or_lfmp",
        cancellation="lfmp_at_official_cancellation_if_not_rescheduled_before_expiration",
        stat_corrections="settlement_delayed_while_official_result_under_review; post_settlement_correction_rule_not_fully_fingerprinted",
        threshold_push="binary_player_prop_threshold; exact_market_line_terms_required",
        source_ref="https://docs.polymarket.us/faqs/sports-faqs",
        complete=False,
    ))
    _PROFILE_ROWS.append(_profile(
        "gemini",
        family,
        participation_rule="last_fair_price_if_active_player_does_not_appear; official_stat_after_appearance",
        official_source="league_governing_event_for_player_stat",
        interruption="postponed_or_rescheduled_remains_open_until_played_or_season_ends; expiration_may_adjust",
        cancellation="market_outcome_review_if_expiration_value_cannot_be_determined",
        stat_corrections="family_terms_stat_correction_rule_not_fully_fingerprinted",
        threshold_push="binary_player_prop_threshold; exact_family_terms_required",
        source_ref="current Gemini MLB player-prop contract description and family terms PDF",
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
    """Assess one venue pair, dynamically removing only the participation blocker."""
    left_name = canonical_venue(left_venue)
    right_name = canonical_venue(right_venue)
    family = str(family or "").strip().lower()
    gate, participation_proven = _participation_alignment(family, proof)

    left = PROFILES.get((left_name, family))
    right = PROFILES.get((right_name, family))
    if left is None or right is None:
        return PropPairAssessment(
            left_name, right_name, family,
            PropCertificationStatus.UNAUDITED,
            gate, participation_proven,
            ("missing_venue_family_rule_profile",),
            False,
        )

    # Participation proof only resolves the family-level start/appearance branch.
    # It does not rewrite the venue profiles or erase other lifecycle differences.
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
        # We know enough to keep it closed, but not enough to call it an exact
        # lifecycle comparison. This is intentionally stricter than CONDITIONAL.
        status = PropCertificationStatus.UNAUDITED

    return PropPairAssessment(
        left_name, right_name, family, status, gate, participation_proven,
        tuple(blockers), exact,
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
    """Static pre-participation matrix for all currently supported prop families."""
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

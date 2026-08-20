"""Official 2026-08-20 rule fingerprints for Kalshi <-> Polymarket US MLB props.

This module deliberately does not grant execution eligibility.  It upgrades the
venue profiles from "incomplete audit" to complete, sourced semantic fingerprints
so the existing fail-closed certification engine can expose the *actual* remaining
rule differences.

Primary sources reviewed:
- Kalshi BASEBALLENTITYSTAT contract terms (MLBRBI.pdf), current 2026 terms.
- Polymarket US ATHLETIC STATISTIC CONTRACTS (ASTATC), product certification
  dated 2026-03-31.

The important result is that these venues remain non-identical after the live
participation gate.  In particular, the statistical-correction windows differ,
and cancellation/outcome-review/source hierarchies are not identical.  Therefore
these profiles are complete=True but must still remain execution_eligible=False
under the base certification engine.
"""
from __future__ import annotations

from typing import MutableMapping

from src import mlb_prop_rule_certifications as base
from src.mlb_prop_rule_equivalence import SUPPORTED_FAMILIES

AUDIT_DATE = "2026-08-20"
KALSHI_SOURCE_REF = "https://assets.kalshi.com/contract_terms/MLBRBI.pdf"
POLYMARKET_SOURCE_REF = (
    "https://www.polymarketexchange.com/files/products/"
    "PMUS%20-%20ASTATC%20-%20%282026.03.31%29.pdf"
)


def official_profiles() -> dict[tuple[str, str], base.PropVenueProfile]:
    """Return complete sourced profiles without mutating global certification state."""
    rows: dict[tuple[str, str], base.PropVenueProfile] = {}
    for family in SUPPORTED_FAMILIES:
        rows[("kalshi", family)] = base._profile(
            "kalshi",
            family,
            participation_rule=(
                "starter_required_for_player_market_then_qualifying_plate_appearance_or_pitch"
            ),
            official_source=(
                "league_governing_body_then_official_scorer_then_mlb_and_named_major_sources"
            ),
            interruption=(
                "official_shortened_game_uses_definitive_official_stats;"
                "preofficial_suspension_not_resumed_within_48h_uses_lfmp"
            ),
            cancellation=(
                "cancelled_postponed_or_suspended_before_official_game_and_not_resumed_"
                "within_48h_uses_lfmp"
            ),
            stat_corrections=(
                "statistics_final_at_game_conclusion;corrections_after_conclusion_ignored"
            ),
            threshold_push="official_stat_compared_with_explicit_binary_operator",
            source_ref=KALSHI_SOURCE_REF,
            complete=True,
        )
        rows[("polymarket", family)] = base._profile(
            "polymarket",
            family,
            participation_rule=(
                "nonparticipation_uses_lfmp;once_participation_begins_official_stats_count"
            ),
            official_source=(
                "official_league_competition_or_designated_provider_primary_then_major_sources"
            ),
            interruption=(
                "official_truncated_event_uses_official_stats;"
                "preofficial_suspension_over_48h_uses_lfmp_or_outcome_review"
            ),
            cancellation=(
                "postponement_may_follow_rescheduled_event_up_to_two_weeks;"
                "cancellation_uses_lfmp_or_outcome_review"
            ),
            stat_corrections=(
                "official_corrections_at_or_before_expiration_count;"
                "corrections_after_expiration_ignored"
            ),
            threshold_push="official_stat_compared_with_explicit_binary_operator",
            source_ref=POLYMARKET_SOURCE_REF,
            complete=True,
        )
    return rows


def install(
    target: MutableMapping[tuple[str, str], base.PropVenueProfile] | None = None,
) -> dict[tuple[str, str], base.PropVenueProfile]:
    """Install official fingerprints into a profile mapping and return installed rows.

    The default target is the base module's live ``PROFILES`` mapping.  Passing a
    separate mapping is useful for tests and offline audit tooling.
    """
    target = base.PROFILES if target is None else target
    rows = official_profiles()
    target.update(rows)
    return rows

from dataclasses import replace
from decimal import Decimal

from src.mlb_prop_rule_certifications import (
    PROFILES,
    PropCertificationStatus,
    PropVenueProfile,
    apply_prop_certification,
    assess_prop_pair,
    prop_audit_matrix,
)
from src.mlb_prop_rule_equivalence import ParticipationProof
from src.universal_prediction_quant import LockOpportunity

D = Decimal


def _lock(yes_venue="kalshi", no_venue="polymarket") -> LockOpportunity:
    return LockOpportunity(
        settlement_key="baseball|mlb|nym,phi|player_prop|full_contest|player|bryce_harper|hits|GE|1",
        category="sports_mlb_prop",
        yes_venue=yes_venue,
        yes_market_id="yes-market",
        no_venue=no_venue,
        no_market_id="no-market",
        unit_cost=D("0.95"),
        unit_profit=D("0.05"),
        max_quantity=D("1"),
        max_capital=D("0.95"),
        max_locked_profit=D("0.05"),
        quote_skew_ms=1000,
        settlement_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
        expected_settlement_minutes=180,
        capital_velocity_score=D("0.1"),
    )


def _batter_proof() -> ParticipationProof:
    return ParticipationProof(
        started=True,
        plate_appearance_recorded=True,
        source="MLB_STATS_API_GAME_123",
    )


def test_static_prop_matrix_covers_five_families_and_nine_pairs_each():
    matrix = prop_audit_matrix()
    assert len(matrix["families"]) == 5
    assert matrix["total_pairs"] == 45
    assert matrix["certified_pairs"] == 0
    assert matrix["read_only"] is True
    assert matrix["order_entry"] is False
    assert "baseball_player_home_runs" in matrix["families"]
    for row in matrix["families"].values():
        assert len(row["sportsbook_vs_prediction"]) == 6
        assert len(row["prediction_vs_prediction"]) == 3
        assert row["certified_pairs"] == 0


def test_kalshi_polymarket_batter_pair_waits_for_live_participation():
    result = assess_prop_pair("kalshi", "polymarket", "baseball_player_hits")
    assert result.status is PropCertificationStatus.PARTICIPATION_PENDING
    assert result.participation_proven is False
    assert "participation" in result.remaining_blockers
    assert result.execution_eligible is False


def test_live_plate_appearance_removes_only_participation_blocker():
    result = assess_prop_pair(
        "kalshi", "polymarket", "baseball_player_hits", _batter_proof()
    )
    assert result.participation_proven is True
    assert "participation" not in result.remaining_blockers
    assert "complete_lifecycle_fingerprint_required" in result.remaining_blockers
    assert result.status is PropCertificationStatus.UNAUDITED
    assert result.execution_eligible is False


def test_gemini_total_bases_remains_fail_closed_after_participation():
    result = assess_prop_pair(
        "kalshi", "gemini", "baseball_player_total_bases", _batter_proof()
    )
    assert result.participation_proven is True
    assert result.status is PropCertificationStatus.UNAUDITED
    assert result.remaining_blockers
    assert result.execution_eligible is False


def test_home_runs_is_audited_but_not_executable_after_participation():
    result = assess_prop_pair(
        "kalshi", "gemini", "baseball_player_home_runs", _batter_proof()
    )
    assert result.participation_proven is True
    assert result.status is PropCertificationStatus.UNAUDITED
    assert "complete_lifecycle_fingerprint_required" in result.remaining_blockers
    assert result.execution_eligible is False
    assert PROFILES[("kalshi", "baseball_player_home_runs")].stat_definition == "official_home_runs_compared_with_threshold"
    assert PROFILES[("gemini", "baseball_player_home_runs")].stat_definition == "official_home_runs_compared_with_threshold"


def test_pitcher_requires_start_and_pitch_before_participation_is_proven():
    partial = ParticipationProof(
        starting_pitcher=True,
        source="MLB_STATS_API_GAME_123",
    )
    result = assess_prop_pair("kalshi", "polymarket", "baseball_player_strikeouts", partial)
    assert result.participation_proven is False
    assert "participation" in result.remaining_blockers

    full = replace(partial, pitch_recorded=True)
    result = assess_prop_pair("kalshi", "polymarket", "baseball_player_strikeouts", full)
    assert result.participation_proven is True
    assert "participation" not in result.remaining_blockers
    assert result.execution_eligible is False


def test_apply_never_upgrades_current_real_prop_pair():
    lock = _lock()
    upgraded, assessment = apply_prop_certification(
        lock, family="baseball_player_hits", proof=_batter_proof()
    )
    assert upgraded.settlement_confidence == "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
    assert assessment.execution_eligible is False


def test_unknown_family_fails_closed():
    result = assess_prop_pair("kalshi", "polymarket", "baseball_player_runs")
    assert result.status is PropCertificationStatus.UNAUDITED
    assert result.execution_eligible is False


def test_engine_can_certify_exact_complete_synthetic_pair(monkeypatch):
    template = PropVenueProfile(
        venue="synthetic-a",
        family="baseball_player_hits",
        participation_rule="qualifying_participation",
        stat_definition="official_hits_compared_with_threshold",
        official_source="mlb_official_stats",
        interruption="settle_recorded_stat",
        cancellation="settle_recorded_stat",
        stat_corrections="final_official_stats",
        threshold_push="binary_integer_threshold",
        source_ref="synthetic-test-only",
        complete=True,
    )
    monkeypatch.setitem(PROFILES, ("synthetic-a", "baseball_player_hits"), template)
    monkeypatch.setitem(
        PROFILES,
        ("synthetic-b", "baseball_player_hits"),
        replace(template, venue="synthetic-b"),
    )
    lock = _lock(yes_venue="synthetic-a", no_venue="synthetic-b")
    upgraded, assessment = apply_prop_certification(
        lock, family="baseball_player_hits", proof=_batter_proof()
    )
    assert assessment.status is PropCertificationStatus.CERTIFIED
    assert assessment.exact_settlement_equivalent is True
    assert assessment.execution_eligible is True
    assert upgraded.settlement_confidence == "FULL_RULES_AUDITED_EXECUTABLE"

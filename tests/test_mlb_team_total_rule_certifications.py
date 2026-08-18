from dataclasses import replace

from src.mlb_team_total_rule_certifications import (
    PROFILES,
    TeamTotalCertificationStatus,
    TeamTotalRuleProfile,
    assess_team_total_pair,
    team_total_audit_matrix,
)


def test_team_total_matrix_covers_nine_pairs_fail_closed():
    matrix = team_total_audit_matrix()
    assert matrix["family"] == "team_total"
    assert matrix["total_pairs"] == 9
    assert matrix["certified_pairs"] == 0
    assert matrix["read_only"] is True
    assert matrix["order_entry"] is False
    assert len(matrix["sportsbook_vs_prediction"]) == 6
    assert len(matrix["prediction_vs_prediction"]) == 3


def test_draftkings_kalshi_team_total_is_conditional():
    result = assess_team_total_pair("sportsbook:draftkings", "kalshi")
    assert result.status is TeamTotalCertificationStatus.CONDITIONAL
    assert result.execution_eligible is False
    assert "minimum_completion" in result.blockers
    assert "cancellation" in result.blockers


def test_gemini_team_total_is_supported_but_not_lifecycle_certified():
    result = assess_team_total_pair("draftkings", "gemini")
    assert result.status is TeamTotalCertificationStatus.CONDITIONAL
    assert "complete_lifecycle_fingerprint_required" in result.blockers
    assert result.execution_eligible is False


def test_unknown_team_total_venue_fails_closed():
    result = assess_team_total_pair("unknown", "kalshi")
    assert result.status is TeamTotalCertificationStatus.UNAUDITED
    assert result.execution_eligible is False


def test_engine_can_certify_exact_complete_synthetic_team_total_pair(monkeypatch):
    template = TeamTotalRuleProfile(
        venue="synthetic-a",
        official_source="mlb_official_stats",
        normal_outcome="named_team_runs_compared_with_line",
        extra_innings="included",
        minimum_completion="completed_game",
        not_started="void",
        interrupted="void",
        cancellation="void",
        push_threshold="push",
        source_ref="synthetic-test-only",
        complete=True,
    )
    monkeypatch.setitem(PROFILES, "synthetic-a", template)
    monkeypatch.setitem(PROFILES, "synthetic-b", replace(template, venue="synthetic-b"))
    result = assess_team_total_pair("synthetic-a", "synthetic-b")
    assert result.status is TeamTotalCertificationStatus.CERTIFIED
    assert result.exact_settlement_equivalent is True
    assert result.execution_eligible is True

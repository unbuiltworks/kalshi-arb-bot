from decimal import Decimal

from src.mlb_game_rule_certifications import (
    CertificationStatus,
    PROFILES,
    SUPPORTED_FAMILIES,
    VenueRuleProfile,
    apply_certification,
    assess_pair,
    audit_matrix,
)
from src.universal_prediction_quant import LockOpportunity

D = Decimal


def _lock(
    yes_venue="sportsbook:draftkings",
    no_venue="kalshi",
    family="moneyline",
) -> LockOpportunity:
    line = "" if family == "moneyline" else "1.5"
    subject_type = "team" if family != "game_total" else "contest"
    subject = "phi" if family != "game_total" else "contest"
    stat = "" if family == "moneyline" else "runs"
    comparator = "YES" if family == "moneyline" else "GT"
    return LockOpportunity(
        settlement_key=f"baseball|mlb|nym,phi|{family}|full_contest|{subject_type}|{subject}|{stat}|{comparator}|{line}",
        category="sports_mlb_game",
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


def test_full_matrix_covers_three_families_and_direct_pairs():
    matrix = audit_matrix()
    assert tuple(matrix["families"]) == SUPPORTED_FAMILIES
    assert matrix["total_pairs"] == 27
    assert matrix["certified_pairs"] == 0
    assert matrix["conditional_pairs"] == 27
    assert matrix["unaudited_pairs"] == 0
    assert matrix["read_only"] is True
    assert matrix["order_entry"] is False
    for family in SUPPORTED_FAMILIES:
        row = matrix["families"][family]
        assert len(row["sportsbook_vs_prediction"]) == 6
        assert len(row["prediction_vs_prediction"]) == 3
        assert row["certified_pairs"] == 0
        assert row["conditional_pairs"] == 9


def test_draftkings_kalshi_moneyline_fails_on_completion_and_tail_rules():
    result = assess_pair("sportsbook:draftkings", "kalshi", "moneyline")
    assert result.status is CertificationStatus.CONDITIONAL
    assert result.execution_eligible is False
    assert result.execution_confidence == "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
    assert "minimum_completion" in result.blockers
    assert "not_started" in result.blockers
    assert "cancellation" in result.blockers


def test_fanduel_kalshi_spread_is_not_execution_certified():
    result = assess_pair("sportsbook:fanduel", "kalshi", "spread")
    assert result.status is CertificationStatus.CONDITIONAL
    assert not result.exact_settlement_equivalent
    assert not result.execution_eligible
    assert "minimum_completion" in result.blockers
    assert "interrupted" in result.blockers


def test_draftkings_polymarket_total_is_not_execution_certified():
    result = assess_pair("sportsbook:draftkings", "polymarket", "game_total")
    assert result.status is CertificationStatus.CONDITIONAL
    assert not result.execution_eligible
    assert "minimum_completion" in result.blockers
    assert "cancellation" in result.blockers
    assert "official_source" in result.blockers


def test_prediction_market_pairs_are_audited_but_not_exact():
    for family in SUPPORTED_FAMILIES:
        for left, right in (("kalshi", "polymarket"), ("kalshi", "gemini"), ("polymarket", "gemini")):
            result = assess_pair(left, right, family)
            assert result.status is CertificationStatus.CONDITIONAL
            assert not result.execution_eligible
            assert result.blockers
            assert "cancellation" in result.blockers


def test_unknown_book_or_family_fails_closed():
    result = assess_pair("sportsbook:unknownbook", "kalshi", "moneyline")
    assert result.status is CertificationStatus.UNAUDITED
    assert not result.execution_eligible
    result = assess_pair("sportsbook:draftkings", "kalshi", "player_prop")
    assert result.status is CertificationStatus.UNAUDITED
    assert not result.execution_eligible


def test_uncertified_real_locks_are_never_upgraded():
    for family in SUPPORTED_FAMILIES:
        original = _lock(family=family)
        upgraded, assessment = apply_certification(original)
        assert upgraded.settlement_confidence == "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
        assert assessment.execution_eligible is False


def test_engine_can_certify_only_exact_synthetic_profiles(monkeypatch):
    template = VenueRuleProfile(
        venue="synthetic-a",
        family="spread",
        official_source="league_official_result",
        normal_outcome="named_team_margin_exceeds_line",
        extra_innings="included",
        minimum_completion="completed_game",
        not_started="void",
        interrupted="void",
        cancellation="void",
        push_tie="push",
        source_ref="synthetic-test-only",
        complete=True,
    )
    monkeypatch.setitem(PROFILES, ("synthetic-a", "spread"), template)
    monkeypatch.setitem(PROFILES, ("synthetic-b", "spread"), VenueRuleProfile(
        **{**template.__dict__, "venue": "synthetic-b"}
    ))
    original = _lock(yes_venue="sportsbook:synthetic-a", no_venue="synthetic-b", family="spread")
    upgraded, assessment = apply_certification(original)
    assert assessment.status is CertificationStatus.CERTIFIED
    assert assessment.exact_settlement_equivalent is True
    assert assessment.execution_eligible is True
    assert upgraded.settlement_confidence == "FULL_RULES_AUDITED_EXECUTABLE"
    assert upgraded is not original

from decimal import Decimal

from src.mlb_game_rule_certifications import (
    CertificationStatus,
    PROFILES,
    VenueRuleProfile,
    apply_certification,
    assess_pair,
    audit_matrix,
)
from src.universal_prediction_quant import LockOpportunity

D = Decimal


def _lock(yes_venue="sportsbook:draftkings", no_venue="kalshi") -> LockOpportunity:
    return LockOpportunity(
        settlement_key="baseball|mlb|nym,phi|moneyline|full_contest|team|phi||YES|",
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


def test_current_real_moneyline_matrix_has_zero_certified_pairs():
    matrix = audit_matrix()
    assert matrix["family"] == "moneyline"
    assert len(matrix["pairs"]) == 6
    assert matrix["certified_pairs"] == 0
    assert matrix["read_only"] is True
    assert matrix["order_entry"] is False
    assert all(row["status"] != "CERTIFIED" for row in matrix["pairs"])


def test_draftkings_kalshi_fails_exact_settlement_on_tail_rules():
    result = assess_pair("sportsbook:draftkings", "kalshi", "moneyline")
    assert result.status in {CertificationStatus.CONDITIONAL, CertificationStatus.REJECTED}
    assert result.execution_eligible is False
    assert result.execution_confidence == "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
    assert "not_started" in result.blockers
    assert "cancellation" in result.blockers


def test_fanduel_kalshi_is_not_execution_certified():
    result = assess_pair("sportsbook:fanduel", "kalshi", "moneyline")
    assert result.status is not CertificationStatus.CERTIFIED
    assert not result.exact_settlement_equivalent
    assert not result.execution_eligible
    assert "interrupted" in result.blockers


def test_unknown_book_or_family_fails_closed():
    result = assess_pair("sportsbook:unknownbook", "kalshi", "moneyline")
    assert result.status is CertificationStatus.UNAUDITED
    assert not result.execution_eligible
    result = assess_pair("sportsbook:draftkings", "kalshi", "spread")
    assert result.status is CertificationStatus.UNAUDITED
    assert not result.execution_eligible


def test_uncertified_real_lock_is_never_upgraded():
    original = _lock()
    upgraded, assessment = apply_certification(original)
    assert upgraded.settlement_confidence == "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
    assert assessment.execution_eligible is False


def test_engine_can_certify_only_exact_synthetic_profiles(monkeypatch):
    template = VenueRuleProfile(
        venue="synthetic-a",
        family="moneyline",
        official_source="league official result",
        normal_winner="named team wins",
        extra_innings="included",
        not_started="void",
        interrupted="void",
        cancellation="void",
        tie_push="push",
        source_ref="synthetic-test-only",
        complete=True,
    )
    monkeypatch.setitem(PROFILES, ("synthetic-a", "moneyline"), template)
    monkeypatch.setitem(PROFILES, ("synthetic-b", "moneyline"), VenueRuleProfile(
        **{**template.__dict__, "venue": "synthetic-b"}
    ))
    original = _lock(yes_venue="sportsbook:synthetic-a", no_venue="synthetic-b")
    upgraded, assessment = apply_certification(original)
    assert assessment.status is CertificationStatus.CERTIFIED
    assert assessment.exact_settlement_equivalent is True
    assert assessment.execution_eligible is True
    assert upgraded.settlement_confidence == "FULL_RULES_AUDITED_EXECUTABLE"
    assert upgraded is not original

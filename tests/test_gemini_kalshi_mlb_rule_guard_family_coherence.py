from src.gemini_kalshi_mlb_rule_guard import (
    FULL_RULES_AUDITED_SHADOW,
    RULES_PENDING,
    classify_mlb_rule_audit,
)


_REQUIRED = (
    "exact_event_identity",
    "threshold_orientation_equivalent",
    "source_hierarchy_equivalent",
    "postponement_reschedule_equivalent",
    "cancellation_forfeit_disqualification_equivalent",
    "suspended_interrupted_game_equivalent",
    "corrections_review_equivalent",
    "expiration_close_equivalent",
)


def _complete(gemini_scope: str, kalshi_scope: str):
    evidence = {field: True for field in _REQUIRED}
    evidence.update(
        {
            "gemini_market_outcome_review_fallback": True,
            "kalshi_market_outcome_review_equivalent": True,
            "gemini_rule_scope_id": gemini_scope,
            "kalshi_rule_scope_id": kalshi_scope,
        }
    )
    evidence["evidence_sources"] = {
        field: {
            "gemini": "https://developer.gemini.com/prediction-markets/tickers-sports",
            "kalshi": "https://kalshi.com/markets/kxmlbtotal",
        }
        for field in _REQUIRED
    }
    evidence["evidence_sources"]["gemini_market_outcome_review_fallback"] = {
        "gemini": "https://www.gemini.com/legal/prediction-market-rules"
    }
    evidence["evidence_sources"]["kalshi_market_outcome_review_equivalent"] = {
        "gemini": "https://www.gemini.com/legal/prediction-market-rules",
        "kalshi": "https://kalshi.com/markets/kxmlbtotal",
    }
    evidence["evidence_fingerprints"] = {
        field: {"gemini": "a" * 64, "kalshi": "b" * 64} for field in _REQUIRED
    }
    evidence["evidence_fingerprints"]["gemini_market_outcome_review_fallback"] = {
        "gemini": "a" * 64
    }
    evidence["evidence_fingerprints"]["kalshi_market_outcome_review_equivalent"] = {
        "gemini": "a" * 64,
        "kalshi": "b" * 64,
    }
    evidence["evidence_scopes"] = {
        field: {"gemini": gemini_scope, "kalshi": kalshi_scope} for field in _REQUIRED
    }
    evidence["evidence_scopes"]["gemini_market_outcome_review_fallback"] = {
        "gemini": gemini_scope
    }
    evidence["evidence_scopes"]["kalshi_market_outcome_review_equivalent"] = {
        "gemini": gemini_scope,
        "kalshi": kalshi_scope,
    }
    return evidence


def test_total_to_total_family_can_reach_shadow_only():
    result = classify_mlb_rule_audit(
        _complete(
            "GEMI-MLB-2608192205-MIA-PHI-T-O9",
            "KXMLBTOTAL-26AUG191805MIAPHI-10",
        )
    )
    assert result.status == FULL_RULES_AUDITED_SHADOW
    assert result.live_probe_ready is False


def test_spread_to_spread_family_can_reach_shadow_only():
    result = classify_mlb_rule_audit(
        _complete(
            "GEMI-MLB-2608192240-SF-CLE-S-CLE3",
            "KXMLBSPREAD-26AUG191840SFCLE-CLE4",
        )
    )
    assert result.status == FULL_RULES_AUDITED_SHADOW
    assert result.live_probe_ready is False


def test_gemini_total_cannot_be_audited_against_kalshi_spread():
    result = classify_mlb_rule_audit(
        _complete(
            "GEMI-MLB-2608192205-MIA-PHI-T-O9",
            "KXMLBSPREAD-26AUG191840SFCLE-CLE4",
        )
    )
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_CROSS_VENUE_FAMILY_MISMATCH" in result.hard_blockers
    assert result.full_rules_audited is False
    assert result.live_probe_ready is False


def test_gemini_spread_cannot_be_audited_against_kalshi_total():
    result = classify_mlb_rule_audit(
        _complete(
            "GEMI-MLB-2608192240-SF-CLE-S-CLE3",
            "KXMLBTOTAL-26AUG191805MIAPHI-10",
        )
    )
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_CROSS_VENUE_FAMILY_MISMATCH" in result.hard_blockers
    assert result.full_rules_audited is False
    assert result.live_probe_ready is False

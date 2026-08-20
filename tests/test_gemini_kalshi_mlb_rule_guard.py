from src.gemini_kalshi_mlb_rule_guard import (
    EXCEPTION_LIFECYCLE_UNRESOLVED_RESEARCH_ONLY,
    FULL_RULES_AUDITED_SHADOW,
    RULES_PENDING,
    classify_mlb_rule_audit,
    current_public_mlb_total_spread_evidence,
)


def _complete_evidence():
    return {
        "exact_event_identity": True,
        "threshold_orientation_equivalent": True,
        "source_hierarchy_equivalent": True,
        "postponement_reschedule_equivalent": True,
        "cancellation_forfeit_disqualification_equivalent": True,
        "suspended_interrupted_game_equivalent": True,
        "corrections_review_equivalent": True,
        "expiration_close_equivalent": True,
        "gemini_market_outcome_review_fallback": True,
        "kalshi_market_outcome_review_equivalent": True,
    }


def test_current_public_evidence_stays_research_only():
    result = classify_mlb_rule_audit(current_public_mlb_total_spread_evidence())
    assert result.status == EXCEPTION_LIFECYCLE_UNRESOLVED_RESEARCH_ONLY
    assert result.full_rules_audited is False
    assert result.fully_audited_shadow_ready is False
    assert result.live_probe_ready is False
    assert "ASYMMETRIC_EXCEPTION_REVIEW_FALLBACK" in result.hard_blockers


def test_all_required_equivalence_can_reach_shadow_only():
    result = classify_mlb_rule_audit(_complete_evidence())
    assert result.status == FULL_RULES_AUDITED_SHADOW
    assert result.full_rules_audited is True
    assert result.fully_audited_shadow_ready is True
    assert result.live_probe_ready is False
    assert result.hard_blockers == ()


def test_string_true_does_not_pass_rule_audit():
    evidence = _complete_evidence()
    evidence["suspended_interrupted_game_equivalent"] = "true"
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert result.full_rules_audited is False
    assert result.live_probe_ready is False
    assert "RULE_AUDIT_MISSING:suspended_interrupted_game_equivalent" in result.hard_blockers


def test_asymmetric_review_fallback_blocks_even_if_other_fields_match():
    evidence = _complete_evidence()
    evidence["kalshi_market_outcome_review_equivalent"] = False
    result = classify_mlb_rule_audit(evidence)
    assert result.status == EXCEPTION_LIFECYCLE_UNRESOLVED_RESEARCH_ONLY
    assert result.live_probe_ready is False
    assert "ASYMMETRIC_EXCEPTION_REVIEW_FALLBACK" in result.hard_blockers

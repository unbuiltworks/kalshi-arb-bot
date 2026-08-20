from src.gemini_kalshi_mlb_rule_guard import (
    EXCEPTION_LIFECYCLE_UNRESOLVED_RESEARCH_ONLY,
    FULL_RULES_AUDITED_SHADOW,
    RULES_PENDING,
    classify_mlb_rule_audit,
    current_public_mlb_total_spread_evidence,
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

_GEMINI_FP = "a" * 64
_KALSHI_FP = "b" * 64
_GEMINI_SCOPE = "GEMI-MLB-2608192205-MIA-PHI-T-O9"
_KALSHI_SCOPE = "KXMLBTOTAL-26AUG191805MIAPHI-10"


def _complete_evidence():
    evidence = {
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
        "gemini_rule_scope_id": _GEMINI_SCOPE,
        "kalshi_rule_scope_id": _KALSHI_SCOPE,
    }
    evidence["evidence_sources"] = {
        field: {
            "gemini": f"https://developer.gemini.com/prediction-markets/{field}",
            "kalshi": f"https://kalshi.com/markets/{field}",
        }
        for field in _REQUIRED
    }
    evidence["evidence_sources"]["gemini_market_outcome_review_fallback"] = {
        "gemini": "https://www.gemini.com/legal/prediction-market-rules",
    }
    evidence["evidence_sources"]["kalshi_market_outcome_review_equivalent"] = {
        "gemini": "https://www.gemini.com/legal/prediction-market-rules",
        "kalshi": "https://kalshi.com/markets/kxmlbtotal",
    }
    evidence["evidence_fingerprints"] = {
        field: {"gemini": _GEMINI_FP, "kalshi": _KALSHI_FP}
        for field in _REQUIRED
    }
    evidence["evidence_fingerprints"]["gemini_market_outcome_review_fallback"] = {
        "gemini": _GEMINI_FP,
    }
    evidence["evidence_fingerprints"]["kalshi_market_outcome_review_equivalent"] = {
        "gemini": _GEMINI_FP,
        "kalshi": _KALSHI_FP,
    }
    evidence["evidence_scopes"] = {
        field: {"gemini": _GEMINI_SCOPE, "kalshi": _KALSHI_SCOPE}
        for field in _REQUIRED
    }
    evidence["evidence_scopes"]["gemini_market_outcome_review_fallback"] = {
        "gemini": _GEMINI_SCOPE,
    }
    evidence["evidence_scopes"]["kalshi_market_outcome_review_equivalent"] = {
        "gemini": _GEMINI_SCOPE,
        "kalshi": _KALSHI_SCOPE,
    }
    return evidence


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
    assert "RULE_AUDIT_MISSING:suspended_interrupted_game_equivalent" in result.hard_blockers


def test_asymmetric_review_fallback_blocks_even_if_other_fields_match():
    evidence = _complete_evidence()
    evidence["kalshi_market_outcome_review_equivalent"] = False
    result = classify_mlb_rule_audit(evidence)
    assert result.status == EXCEPTION_LIFECYCLE_UNRESOLVED_RESEARCH_ONLY
    assert "ASYMMETRIC_EXCEPTION_REVIEW_FALLBACK" in result.hard_blockers


def test_omitted_gemini_review_fact_cannot_silently_pass():
    evidence = _complete_evidence()
    evidence.pop("gemini_market_outcome_review_fallback")
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_MISSING:gemini_market_outcome_review_fallback" in result.hard_blockers


def test_gemini_review_fact_requires_official_gemini_source():
    evidence = _complete_evidence()
    evidence["evidence_sources"].pop("gemini_market_outcome_review_fallback")
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_MISSING_SOURCE:gemini_market_outcome_review_fallback" in result.hard_blockers


def test_kalshi_review_equivalence_requires_bilateral_source():
    evidence = _complete_evidence()
    evidence["evidence_sources"]["kalshi_market_outcome_review_equivalent"] = {
        "kalshi": "https://kalshi.com/markets/kxmlbtotal",
    }
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_MISSING_SOURCE:kalshi_market_outcome_review_equivalent" in result.hard_blockers


def test_bare_booleans_without_bilateral_sources_cannot_pass():
    evidence = _complete_evidence()
    evidence.pop("evidence_sources")
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_MISSING_SOURCE:exact_event_identity" in result.hard_blockers


def test_one_sided_source_reference_fails_closed():
    evidence = _complete_evidence()
    evidence["evidence_sources"]["corrections_review_equivalent"] = {
        "gemini": "https://developer.gemini.com/prediction-markets/corrections",
        "kalshi": "",
    }
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_MISSING_SOURCE:corrections_review_equivalent" in result.hard_blockers


def test_third_party_or_placeholder_sources_fail_closed():
    evidence = _complete_evidence()
    evidence["evidence_sources"]["expiration_close_equivalent"] = {
        "gemini": "https://example.test/gemini/expiration",
        "kalshi": "https://kalshi.com/markets/expiration",
    }
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_MISSING_SOURCE:expiration_close_equivalent" in result.hard_blockers


def test_non_https_official_source_fails_closed():
    evidence = _complete_evidence()
    evidence["evidence_sources"]["source_hierarchy_equivalent"] = {
        "gemini": "http://developer.gemini.com/prediction-markets/source-hierarchy",
        "kalshi": "https://kalshi.com/markets/source-hierarchy",
    }
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_MISSING_SOURCE:source_hierarchy_equivalent" in result.hard_blockers


def test_bilateral_sources_without_content_fingerprints_cannot_pass():
    evidence = _complete_evidence()
    evidence.pop("evidence_fingerprints")
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert result.full_rules_audited is False
    assert "RULE_AUDIT_MISSING_FINGERPRINT:exact_event_identity" in result.hard_blockers
    assert "RULE_AUDIT_MISSING_FINGERPRINT:gemini_market_outcome_review_fallback" in result.hard_blockers
    assert "RULE_AUDIT_MISSING_FINGERPRINT:kalshi_market_outcome_review_equivalent" in result.hard_blockers


def test_malformed_fingerprint_fails_closed():
    evidence = _complete_evidence()
    evidence["evidence_fingerprints"]["expiration_close_equivalent"]["kalshi"] = "not-a-sha256"
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_MISSING_FINGERPRINT:expiration_close_equivalent" in result.hard_blockers


def test_missing_exact_scope_ids_fail_closed():
    evidence = _complete_evidence()
    evidence.pop("gemini_rule_scope_id")
    evidence.pop("kalshi_rule_scope_id")
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_MISSING_SCOPE_ID:gemini" in result.hard_blockers
    assert "RULE_AUDIT_MISSING_SCOPE_ID:kalshi" in result.hard_blockers


def test_generic_or_wrong_family_scope_ids_fail_closed():
    evidence = _complete_evidence()
    evidence["gemini_rule_scope_id"] = "MLB-GENERIC"
    evidence["kalshi_rule_scope_id"] = "KXMLBHIT"
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_MISSING_SCOPE_ID:gemini" in result.hard_blockers
    assert "RULE_AUDIT_MISSING_SCOPE_ID:kalshi" in result.hard_blockers


def test_mismatched_field_scope_cannot_certify_unrelated_contract():
    evidence = _complete_evidence()
    evidence["evidence_scopes"]["corrections_review_equivalent"]["kalshi"] = (
        "KXMLBSPREAD-26AUG191840SFCLE-CLE4"
    )
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert "RULE_AUDIT_SCOPE_MISMATCH:corrections_review_equivalent" in result.hard_blockers


def test_exception_fallback_must_be_bound_to_exact_gemini_scope():
    evidence = _complete_evidence()
    evidence["evidence_scopes"]["gemini_market_outcome_review_fallback"]["gemini"] = (
        "GEMI-MLB-2608192240-SF-CLE-S-CLE3"
    )
    result = classify_mlb_rule_audit(evidence)
    assert result.status == RULES_PENDING
    assert (
        "RULE_AUDIT_SCOPE_MISMATCH:gemini_market_outcome_review_fallback"
        in result.hard_blockers
    )

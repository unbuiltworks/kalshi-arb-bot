from src.gemini_kalshi_settlement_source_guard import (
    BTC15M_CURRENT_CLASSIFICATION,
    SettlementSourceEvidence,
    classify_settlement_sources,
)


def test_current_btc15m_provider_mismatch_is_hard_block():
    result = BTC15M_CURRENT_CLASSIFICATION
    assert result.status == "SETTLEMENT_SOURCE_MISMATCH_CONFIRMED_RESEARCH_ONLY"
    assert result.hard_block is True
    assert result.exact_source_match is False


def test_missing_source_evidence_fails_closed():
    result = classify_settlement_sources(
        SettlementSourceEvidence("Kaiko", "GRR-KAIKO_BTCUSD_60S"),
        SettlementSourceEvidence("CF Benchmarks", None),
    )
    assert result.status == "SETTLEMENT_SOURCE_EVIDENCE_INCOMPLETE_RESEARCH_ONLY"
    assert result.hard_block is True


def test_exact_source_match_still_requires_methodology_audit():
    evidence = SettlementSourceEvidence("Example Agency", "EXAMPLE_INDEX")
    result = classify_settlement_sources(evidence, evidence)
    assert result.status == "SETTLEMENT_SOURCE_MATCH_CANDIDATE_NEEDS_METHOD_AUDIT"
    assert result.exact_source_match is True
    assert result.hard_block is True

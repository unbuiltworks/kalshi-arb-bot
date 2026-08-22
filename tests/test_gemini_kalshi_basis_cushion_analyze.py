from src.gemini_kalshi_basis_cushion_analyze import (
    DIR_GYES_KNO,
    DIR_KYES_GNO,
    analyze_row,
    summarize,
)


def test_incident_geometry_is_not_a_safe_direction_breach_but_remains_blocked():
    row = {
        "asset": "BTC",
        "gemini_strike": "76863.63",
        "kalshi_target": "77325.16",
        "gemini_settlement": "77275.31415094339",
        "kalshi_expiration_value": "77270.26",
        "dominance_safe_direction_shared_value": DIR_GYES_KNO,
    }
    out = analyze_row(row)
    assert out["evidence_complete"] is True
    assert out["numeric_gemini_outcome"] == "yes"
    assert out["numeric_kalshi_outcome"] == "no"
    assert out["cross_index_dominance_breached"] is False
    # Kalshi finished below Gemini, which is favorable rather than adverse for
    # G-YES + K-NO when K target > G strike.
    assert out["adverse_terminal_basis_value"] == "0"
    assert out["terminal_cushion_sufficient"] is True
    assert out["blocker"] == "INDEX_MISMATCH_RESEARCH_ONLY"


def test_cross_index_divergence_can_defeat_shared_value_safe_direction():
    row = {
        "asset": "BTC",
        "gemini_strike": "100",
        "kalshi_target": "101",
        "gemini_settlement": "99.9",
        "kalshi_expiration_value": "101.1",
        "dominance_safe_direction_shared_value": DIR_GYES_KNO,
    }
    out = analyze_row(row)
    assert out["numeric_gemini_outcome"] == "no"
    assert out["numeric_kalshi_outcome"] == "yes"
    assert out["cross_index_dominance_breached"] is True
    assert float(out["adverse_basis_to_threshold_gap_ratio"]) > 1
    assert out["terminal_cushion_sufficient"] is False
    assert out["blocker"] == "CROSS_INDEX_DOMINANCE_BREACH_OBSERVED"


def test_reverse_safe_direction_can_also_be_defeated():
    row = {
        "asset": "ETH",
        "gemini_strike": "101",
        "kalshi_target": "100",
        "gemini_settlement": "101.2",
        "kalshi_expiration_value": "99.8",
        "dominance_safe_direction_shared_value": DIR_KYES_GNO,
    }
    out = analyze_row(row)
    assert out["cross_index_dominance_breached"] is True
    assert float(out["adverse_terminal_basis_value"]) > 0


def test_favorable_basis_is_not_counted_as_adverse():
    row = {
        "asset": "SOL",
        "gemini_strike": "100",
        "kalshi_target": "101",
        "gemini_settlement": "100.5",
        "kalshi_expiration_value": "99.5",
        "dominance_safe_direction_shared_value": DIR_GYES_KNO,
    }
    out = analyze_row(row)
    assert out["adverse_terminal_basis_value"] == "0"
    assert out["adverse_basis_to_threshold_gap_ratio"] == "0E+2" or float(out["adverse_basis_to_threshold_gap_ratio"]) == 0
    assert out["terminal_cushion_sufficient"] is True


def test_missing_numeric_terminal_evidence_fails_closed():
    out = analyze_row({
        "asset": "SOL",
        "gemini_strike": "100",
        "kalshi_target": "101",
        "gemini_settlement": "100.5",
        "dominance_safe_direction_shared_value": DIR_GYES_KNO,
    })
    assert out["evidence_complete"] is False
    assert out["cross_index_dominance_breached"] is None
    assert out["blocker"] == "MISSING_NUMERIC_THRESHOLD_OR_FINAL_VALUE"


def test_summary_never_promotes_even_when_no_breach_is_observed():
    rows = [{
        "asset": "XRP",
        "gemini_strike": "1",
        "kalshi_target": "1.01",
        "gemini_settlement": "1.02",
        "kalshi_expiration_value": "1.02",
        "dominance_safe_direction_shared_value": DIR_GYES_KNO,
    }]
    out = summarize(rows)
    assert out["cross_index_dominance_breaches"] == 0
    assert out["overall"]["terminal_cushion_sufficient"] == 1
    assert out["hard_blocker"] == "INDEX_MISMATCH_RESEARCH_ONLY"
    assert out["trade_authorized"] is False
    assert out["order_entry"] is False

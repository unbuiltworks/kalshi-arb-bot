from src.weather_settlement_rule_semantics_guard import audit_weather_settlement_rule_semantics


EXACT_MARKET_URL = "https://kalshi.com/markets/kxhighmia-26aug25/miami-high-temperature"


def _row():
    return {
        "rounding_tie_semantics_verified": True,
        "correction_revision_semantics_verified": True,
        "settlement_lifecycle_verified": True,
        "terminal_value_transform": "AS_PUBLISHED",
        "settlement_lifecycle_evidence_url": EXACT_MARKET_URL,
        "exact_market_evidence_url": EXACT_MARKET_URL,
    }


def test_clean_rule_semantics_are_read_only_verified():
    out = audit_weather_settlement_rule_semantics(_row())
    assert out["settlement_rule_semantics_verified"] is True
    assert out["rule_evidence_bound_to_exact_market"] is True
    assert out["blockers"] == []
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_string_true_does_not_verify_rule_semantics():
    row = _row()
    row["rounding_tie_semantics_verified"] = "true"
    out = audit_weather_settlement_rule_semantics(row)
    assert out["settlement_rule_semantics_verified"] is False
    assert "ROUNDING_TIE_SEMANTICS_VERIFIED_REQUIRED" in out["blockers"]


def test_missing_correction_or_lifecycle_evidence_fails_closed():
    row = _row()
    row.pop("correction_revision_semantics_verified")
    row["settlement_lifecycle_evidence_url"] = ""
    out = audit_weather_settlement_rule_semantics(row)
    assert out["settlement_rule_semantics_verified"] is False
    assert "CORRECTION_REVISION_SEMANTICS_VERIFIED_REQUIRED" in out["blockers"]
    assert "SETTLEMENT_LIFECYCLE_OFFICIAL_SOURCE_REQUIRED" in out["blockers"]


def test_terminal_value_transform_must_be_explicit():
    row = _row()
    row["terminal_value_transform"] = None
    out = audit_weather_settlement_rule_semantics(row)
    assert out["settlement_rule_semantics_verified"] is False
    assert "TERMINAL_VALUE_TRANSFORM_EVIDENCE_REQUIRED" in out["blockers"]


def test_unaudited_terminal_transform_fails_closed():
    row = _row()
    row["terminal_value_transform"] = "ROUND_NEAREST_INTEGER"
    out = audit_weather_settlement_rule_semantics(row)
    assert out["settlement_rule_semantics_verified"] is False
    assert "TERMINAL_VALUE_TRANSFORM_UNAUDITED" in out["blockers"]


def test_third_party_or_lookalike_lifecycle_source_fails_closed():
    for url in (
        "https://example.com/kalshi-weather-rules",
        "https://kalshi.com.example.com/markets/kxhighmia-26aug25",
        "http://kalshi.com/markets/kxhighmia-26aug25",
        "https://kalshi.com/",
    ):
        row = _row()
        row["settlement_lifecycle_evidence_url"] = url
        out = audit_weather_settlement_rule_semantics(row)
        assert out["settlement_rule_semantics_verified"] is False
        assert "SETTLEMENT_LIFECYCLE_OFFICIAL_SOURCE_REQUIRED" in out["blockers"]


def test_rule_evidence_must_match_exact_market_evidence():
    row = _row()
    row["settlement_lifecycle_evidence_url"] = "https://kalshi.com/markets/kxhighny-26aug25/new-york-high-temperature"
    out = audit_weather_settlement_rule_semantics(row)
    assert out["settlement_rule_semantics_verified"] is False
    assert out["rule_evidence_bound_to_exact_market"] is False
    assert "SETTLEMENT_RULE_EVIDENCE_NOT_BOUND_TO_EXACT_MARKET" in out["blockers"]


def test_missing_exact_market_evidence_fails_closed():
    row = _row()
    row.pop("exact_market_evidence_url")
    out = audit_weather_settlement_rule_semantics(row)
    assert out["settlement_rule_semantics_verified"] is False
    assert "SETTLEMENT_RULE_EXACT_MARKET_EVIDENCE_REQUIRED" in out["blockers"]


def test_nested_exact_market_evidence_is_supported_but_rechecked():
    row = _row()
    row.pop("exact_market_evidence_url")
    row["exact_market_source_audit"] = {"verified": True, "evidence_url": EXACT_MARKET_URL}
    out = audit_weather_settlement_rule_semantics(row)
    assert out["settlement_rule_semantics_verified"] is True
    assert out["rule_evidence_bound_to_exact_market"] is True

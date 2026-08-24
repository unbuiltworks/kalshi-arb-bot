from src.weather_certification_dimensions import audit_weather_certification_dimensions


def _clean_row():
    return {
        "blockers": [],
        "exact_market_source_verified": True,
        "settlement_certified_shadow": True,
        "quote_age_ms": 100,
        "max_quote_age_ms": 2_000,
        "yes_ask_cents": 40,
        "yes_available_contracts": 5,
        "no_ask_cents": 61,
        "no_available_contracts": 5,
        "fee_type": "quadratic",
        "fee_multiplier": 1,
        "fee_metadata_source": "SERIES_METADATA",
        "fee_schedule_checked": True,
        "forecast_bracket_probability": "0.55",
        "model_sanity_verified": True,
        "model_provider_provenance_verified": True,
        "local_day_binding_verified": True,
        "forecast_valid_local_day_verified": True,
        "anchor_valid_local_day_verified": True,
        "model_run_quote_time_verified": True,
        "ev_certified_shadow": True,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }


def test_current_runtime_weather_blockers_stay_research_only():
    row = _clean_row()
    row["readiness_blockers"] = [
        "TWC_SOURCE_ACCESS_REQUIRED",
        "MODEL_RESEARCH_ONLY",
        "EXACT_MARKET_SOURCE_AUDIT_REQUIRED",
        "AUTHORITATIVE_FEE_AUDIT_REQUIRED",
        "MODEL_SANITY_AUDIT_REQUIRED",
    ]
    row.pop("blockers")
    row["exact_market_source_verified"] = False
    row["settlement_certified_shadow"] = False
    row["ev_certified_shadow"] = False

    out = audit_weather_certification_dimensions(row)

    assert out["settlement_rules_provenance_verified"] is False
    assert out["fee_economics_verified"] is False
    assert out["model_probability_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False
    assert out["live_probe_ready"] is False
    assert out["order_entry"] is False


def test_fee_blocker_does_not_erase_settlement_provenance_dimension():
    row = _clean_row()
    row["blockers"] = ["AUTHORITATIVE_FEE_AUDIT_REQUIRED"]
    row["ev_certified_shadow"] = False

    out = audit_weather_certification_dimensions(row)

    assert out["settlement_rules_provenance_verified"] is True
    assert out["fee_economics_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False


def test_exact_market_source_must_be_literal_true():
    for value in (None, False, "true", 1):
        row = _clean_row()
        row["exact_market_source_verified"] = value
        row["settlement_certified_shadow"] = False
        row["ev_certified_shadow"] = False
        out = audit_weather_certification_dimensions(row)
        assert out["settlement_rules_provenance_verified"] is False
        assert "EXACT_MARKET_SOURCE_AUDIT_REQUIRED" in out["settlement_blockers"]


def test_only_fully_certified_read_only_shadow_can_clear_prereqs():
    out = audit_weather_certification_dimensions(_clean_row())

    assert out["settlement_rules_provenance_verified"] is True
    assert out["market_data_verified"] is True
    assert out["fee_economics_verified"] is True
    assert out["model_probability_verified"] is True
    assert out["executable_shadow_prereqs_verified"] is True
    assert out["live_probe_ready"] is False


def test_unknown_blocker_fails_closed():
    row = _clean_row()
    row["blockers"] = ["SOMETHING_NEW_AND_UNCLASSIFIED"]

    out = audit_weather_certification_dimensions(row)

    assert out["unclassified_blockers"] == ["SOMETHING_NEW_AND_UNCLASSIFIED"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_string_boolean_cannot_clear_upstream_ev_gate():
    row = _clean_row()
    row["ev_certified_shadow"] = "true"

    out = audit_weather_certification_dimensions(row)

    assert out["executable_shadow_prereqs_verified"] is False


def test_absence_of_blockers_is_not_affirmative_certification_evidence():
    row = {
        "blockers": [],
        "exact_market_source_verified": True,
        "ev_certified_shadow": True,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }

    out = audit_weather_certification_dimensions(row)

    assert out["settlement_rules_provenance_verified"] is False
    assert out["market_data_verified"] is False
    assert out["fee_economics_verified"] is False
    assert out["model_probability_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False
    assert "SETTLEMENT_CERTIFICATION_EVIDENCE_REQUIRED" in out["settlement_blockers"]
    assert "EXECUTABLE_BOOK_EVIDENCE_REQUIRED" in out["market_data_blockers"]
    assert "AUTHORITATIVE_FEE_EVIDENCE_REQUIRED" in out["fee_blockers"]
    assert "MODEL_AUDIT_CHAIN_EVIDENCE_REQUIRED" in out["model_blockers"]


def test_stringly_dimension_evidence_fails_closed():
    row = _clean_row()
    row["settlement_certified_shadow"] = "true"
    row["fee_schedule_checked"] = "true"
    row["model_sanity_verified"] = "true"

    out = audit_weather_certification_dimensions(row)

    assert out["settlement_rules_provenance_verified"] is False
    assert out["fee_economics_verified"] is False
    assert out["model_probability_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False


def test_stale_or_missing_book_evidence_fails_market_data_dimension():
    stale = _clean_row()
    stale["quote_age_ms"] = 2_001
    out = audit_weather_certification_dimensions(stale)
    assert out["market_data_verified"] is False

    missing = _clean_row()
    missing["yes_ask_cents"] = None
    missing["yes_available_contracts"] = None
    missing["no_ask_cents"] = None
    missing["no_available_contracts"] = None
    out = audit_weather_certification_dimensions(missing)
    assert out["market_data_verified"] is False

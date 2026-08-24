from src.weather_certification_dimensions import audit_weather_certification_dimensions


def _clean_row():
    return {
        "blockers": [],
        "exact_market_source_verified": True,
        "settlement_certified_shadow": True,
        "series_ticker": "KXHIGHMIA",
        "quote_age_ms": 100,
        "max_quote_age_ms": 2_000,
        "quote_observed_at": "2026-08-24T04:00:00Z",
        "quote_age_evaluated_at": "2026-08-24T04:00:00.100Z",
        "yes_ask_cents": 40,
        "yes_available_contracts": 5,
        "no_ask_cents": 61,
        "no_available_contracts": 5,
        "fee_type": "quadratic",
        "fee_multiplier": 1,
        "fee_metadata_source": "series_current",
        "fee_schedule_checked": True,
        "fee_schedule_feed_complete": True,
        "fee_series_ticker": "KXHIGHMIA",
        "fee_schedule_observed_at": "2026-08-24T04:00:01Z",
        "fee_resolution_verified": True,
        "fee_resolution_series_ticker": "KXHIGHMIA",
        "fee_resolution_fee_type": "quadratic",
        "fee_resolution_fee_multiplier": 1,
        "fee_resolution_source": "series_current",
        "fee_resolution_observed_at": "2026-08-24T04:00:01Z",
        "fee_resolution_effective_at": "2026-08-20T00:00:00Z",
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
    assert out["quote_freshness_binding_verified"] is True
    assert out["fee_economics_verified"] is True
    assert out["fee_resolution_binding_verified"] is True
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
    stale["quote_age_evaluated_at"] = "2026-08-24T04:00:02.001Z"
    out = audit_weather_certification_dimensions(stale)
    assert out["market_data_verified"] is False

    missing = _clean_row()
    missing["yes_ask_cents"] = None
    missing["yes_available_contracts"] = None
    missing["no_ask_cents"] = None
    missing["no_available_contracts"] = None
    out = audit_weather_certification_dimensions(missing)
    assert out["market_data_verified"] is False


def test_fixed_point_q1_book_can_clear_market_data_dimension():
    row = _clean_row()
    row["yes_ask_cents"] = None
    row["yes_available_contracts"] = None
    row["no_ask_cents"] = None
    row["no_available_contracts"] = None
    row["yes_ask_dollars"] = "0.3845"
    row["yes_ask_qty_fp"] = "1.25"

    out = audit_weather_certification_dimensions(row)

    assert out["market_data_verified"] is True
    assert out["executable_shadow_prereqs_verified"] is True


def test_fixed_point_sub_q1_depth_does_not_clear_executable_book_dimension():
    row = _clean_row()
    row["yes_ask_cents"] = None
    row["yes_available_contracts"] = None
    row["no_ask_cents"] = None
    row["no_available_contracts"] = None
    row["yes_ask_dollars"] = "0.3845"
    row["yes_ask_qty_fp"] = "0.99"

    out = audit_weather_certification_dimensions(row)

    assert out["market_data_verified"] is False
    assert "EXECUTABLE_BOOK_EVIDENCE_REQUIRED" in out["market_data_blockers"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_malformed_fixed_point_book_fails_closed():
    for price, qty in (("NaN", "5"), ("1.0000", "5"), ("0.5000", "Infinity"), ("0.5000", True)):
        row = _clean_row()
        row["yes_ask_cents"] = None
        row["yes_available_contracts"] = None
        row["no_ask_cents"] = None
        row["no_available_contracts"] = None
        row["yes_ask_dollars"] = price
        row["yes_ask_qty_fp"] = qty

        out = audit_weather_certification_dimensions(row)
        assert out["market_data_verified"] is False
        assert out["executable_shadow_prereqs_verified"] is False


def test_fee_schedule_must_be_complete_and_bound_to_exact_series_and_quote():
    base = _clean_row()
    cases = [
        ("missing completeness", {"fee_schedule_feed_complete": False}),
        ("string completeness", {"fee_schedule_feed_complete": "true"}),
        ("wrong series", {"fee_series_ticker": "KXHIGHNY"}),
        ("unknown source", {"fee_metadata_source": "SERIES_METADATA"}),
        ("schedule observed before quote", {"fee_schedule_observed_at": "2026-08-24T03:59:59Z"}),
        ("naive schedule timestamp", {"fee_schedule_observed_at": "2026-08-24T04:00:01"}),
    ]
    for _, patch in cases:
        row = dict(base)
        row.update(patch)
        out = audit_weather_certification_dimensions(row)
        assert out["fee_economics_verified"] is False
        assert "AUTHORITATIVE_FEE_EVIDENCE_REQUIRED" in out["fee_blockers"]
        assert out["executable_shadow_prereqs_verified"] is False


def test_canonical_historical_or_scheduled_fee_source_can_clear_when_bound():
    for source in ("historical_fee_change", "scheduled_fee_change"):
        row = _clean_row()
        row["fee_metadata_source"] = source
        row["fee_resolution_source"] = source
        out = audit_weather_certification_dimensions(row)
        assert out["fee_economics_verified"] is True
        assert out["fee_resolution_binding_verified"] is True


def test_fee_dimension_requires_literal_quote_time_resolution_verification():
    for value in (None, False, "true", 1):
        row = _clean_row()
        row["fee_resolution_verified"] = value
        out = audit_weather_certification_dimensions(row)
        assert out["fee_economics_verified"] is False
        assert out["fee_resolution_binding_verified"] is False
        assert "FEE_RESOLUTION_NOT_VERIFIED" in out["fee_blockers"]
        assert out["executable_shadow_prereqs_verified"] is False


def test_fee_dimension_rejects_resolver_metadata_that_does_not_match_scored_fee():
    cases = [
        {"fee_resolution_series_ticker": "KXHIGHNY"},
        {"fee_resolution_fee_multiplier": "0.5"},
        {"fee_resolution_source": "scheduled_fee_change"},
        {"fee_resolution_effective_at": "2026-08-24T04:00:02Z"},
        {"fee_resolution_observed_at": "2026-08-24T03:59:59Z"},
    ]
    for patch in cases:
        row = _clean_row()
        row.update(patch)
        out = audit_weather_certification_dimensions(row)
        assert out["fee_economics_verified"] is False
        assert out["fee_resolution_binding_verified"] is False
        assert out["executable_shadow_prereqs_verified"] is False


def test_plausible_fee_fields_without_resolver_binding_cannot_clear_fee_dimension():
    row = _clean_row()
    for key in list(row):
        if key.startswith("fee_resolution_"):
            row.pop(key)

    out = audit_weather_certification_dimensions(row)

    assert out["fee_economics_verified"] is False
    assert out["fee_resolution_binding_verified"] is False
    assert "AUTHORITATIVE_FEE_EVIDENCE_REQUIRED" in out["fee_blockers"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_quote_age_must_be_bound_to_exact_timestamps():
    row = _clean_row()
    row["quote_age_ms"] = 100
    row["quote_age_evaluated_at"] = "2026-08-24T04:00:00.900Z"

    out = audit_weather_certification_dimensions(row)

    assert out["quote_freshness_binding_verified"] is False
    assert out["market_data_verified"] is False
    assert "QUOTE_AGE_TIMESTAMP_MISMATCH" in out["market_data_blockers"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_future_or_missing_quote_timing_cannot_clear_market_data_dimension():
    future = _clean_row()
    future["quote_age_evaluated_at"] = "2026-08-24T03:59:59Z"
    out = audit_weather_certification_dimensions(future)
    assert out["quote_freshness_binding_verified"] is False
    assert "QUOTE_TIMESTAMP_IN_FUTURE" in out["market_data_blockers"]
    assert out["market_data_verified"] is False

    missing = _clean_row()
    missing.pop("quote_age_evaluated_at")
    out = audit_weather_certification_dimensions(missing)
    assert out["quote_freshness_binding_verified"] is False
    assert "QUOTE_FRESHNESS_TIME_UNVERIFIED" in out["market_data_blockers"]
    assert out["market_data_verified"] is False

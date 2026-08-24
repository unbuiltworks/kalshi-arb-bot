from src.weather_certification_dimensions import audit_weather_certification_dimensions


def _clean_row():
    return {"blockers": [], "exact_market_source_verified": True, "settlement_certified_shadow": True, "series_ticker": "KXHIGHMIA", "quote_age_ms": 100, "max_quote_age_ms": 2000, "quote_observed_at": "2026-08-24T04:00:00Z", "quote_age_evaluated_at": "2026-08-24T04:00:00.100Z", "yes_ask_cents": 40, "yes_available_contracts": 5, "no_ask_cents": 61, "no_available_contracts": 5, "fee_type": "quadratic", "fee_multiplier": 1, "fee_metadata_source": "series_current", "fee_schedule_checked": True, "fee_schedule_feed_complete": True, "fee_series_ticker": "KXHIGHMIA", "fee_schedule_observed_at": "2026-08-24T04:00:01Z", "fee_resolution_verified": True, "fee_resolution_series_ticker": "KXHIGHMIA", "fee_resolution_fee_type": "quadratic", "fee_resolution_fee_multiplier": 1, "fee_resolution_source": "series_current", "fee_resolution_observed_at": "2026-08-24T04:00:01Z", "fee_resolution_effective_at": "2026-08-20T00:00:00Z", "forecast_bracket_probability": "0.55", "model_sanity_verified": True, "model_provider_provenance_verified": True, "local_day_binding_verified": True, "forecast_valid_local_day_verified": True, "anchor_valid_local_day_verified": True, "model_run_quote_time_verified": True, "ev_certified_shadow": True, "read_only": True, "order_entry": False, "live_probe_ready": False}


def test_clean_row_clears_read_only_prereqs():
    out = audit_weather_certification_dimensions(_clean_row())
    assert out["blocker_telemetry_verified"] is True
    assert out["executable_shadow_prereqs_verified"] is True
    assert out["order_entry"] is False and out["live_probe_ready"] is False


def test_readiness_blockers_used_only_when_blockers_absent():
    row = _clean_row(); row.pop("blockers"); row["readiness_blockers"] = ["TWC_SOURCE_ACCESS_REQUIRED"]
    out = audit_weather_certification_dimensions(row)
    assert out["blocker_source"] == "readiness_blockers"
    assert out["settlement_rules_provenance_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False


def test_explicit_empty_blockers_take_precedence_over_readiness_blockers():
    row = _clean_row(); row["readiness_blockers"] = ["TWC_SOURCE_ACCESS_REQUIRED"]
    out = audit_weather_certification_dimensions(row)
    assert out["blocker_source"] == "blockers"
    assert out["blocker_telemetry_verified"] is True
    assert out["settlement_rules_provenance_verified"] is True


def test_malformed_explicit_blocker_telemetry_fails_closed_without_fallback():
    for value in ("", 0, False, None, {}):
        row = _clean_row(); row["blockers"] = value; row["readiness_blockers"] = []
        out = audit_weather_certification_dimensions(row)
        assert out["blocker_telemetry_verified"] is False
        assert out["blocker_source"] == "blockers"
        assert out["unclassified_blockers"] == ["MALFORMED_BLOCKER_TELEMETRY"]
        assert out["executable_shadow_prereqs_verified"] is False
        assert out["order_entry"] is False and out["live_probe_ready"] is False


def test_unknown_blocker_fails_closed():
    row = _clean_row(); row["blockers"] = ["SOMETHING_NEW_AND_UNCLASSIFIED"]
    out = audit_weather_certification_dimensions(row)
    assert out["unclassified_blockers"] == ["SOMETHING_NEW_AND_UNCLASSIFIED"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_fixed_point_sub_q1_depth_does_not_clear_book():
    row = _clean_row(); row.update({"yes_ask_cents": None, "yes_available_contracts": None, "no_ask_cents": None, "no_available_contracts": None, "yes_ask_dollars": "0.3845", "yes_ask_qty_fp": "0.99"})
    out = audit_weather_certification_dimensions(row)
    assert out["market_data_verified"] is False


def test_fee_resolution_binding_is_required():
    row = _clean_row(); row["fee_resolution_verified"] = "true"
    out = audit_weather_certification_dimensions(row)
    assert out["fee_economics_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False


def test_quote_timestamp_binding_is_required():
    row = _clean_row(); row["quote_age_evaluated_at"] = "2026-08-24T04:00:00.900Z"
    out = audit_weather_certification_dimensions(row)
    assert out["market_data_verified"] is False
    assert out["quote_freshness_binding_verified"] is False

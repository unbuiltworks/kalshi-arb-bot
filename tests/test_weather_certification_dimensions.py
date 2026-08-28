from copy import deepcopy

from src.weather_certification_dimensions import audit_weather_certification_dimensions


def _model_audits():
    safety = {"blockers": [], "read_only": True, "order_entry": False, "live_probe_ready": False}
    return {
        "model_sanity_audit": {
            **safety,
            "model_sanity_verified": True,
            "model_id": "wx-ensemble-v1",
            "model_provider": "ENSEMBLE",
            "model_component_providers": ["DWD", "ECMWF"],
            "calibration_model_id": "wx-ensemble-v1",
            "calibration_target": "DAILY_MAX_TEMPERATURE_F",
            "calibration_evidence_id": "cal-miami-v1",
            "calibration_sample_count": 120,
            "calibration_station_id": "CLIMIA",
            "calibration_window_end": "2026-08-23T00:00:00+00:00",
            "independent_anchor_source_id": "nws-miami-forecast",
            "independent_anchor_provider": "NOAA",
            "independent_anchor_model_id": "NWS_HUMAN_FORECAST",
            "independent_anchor_provenance_url": "https://weather.gov/mfl/",
            "independent_anchor_observed_at": "2026-08-24T03:30:00+00:00",
        },
        "model_provider_provenance_audit": {
            **safety,
            "provider_provenance_verified": True,
            "model_provider_family": "ENSEMBLE",
            "model_component_provider_families": ["DWD", "ECMWF"],
            "independent_anchor_provider_family": "NOAA",
        },
        "local_day_binding_audit": {
            **safety,
            "local_day_binding_verified": True,
            "market_local_date": "2026-08-24",
            "model_local_date": "2026-08-24",
            "independent_anchor_local_date": "2026-08-24",
        },
        "forecast_valid_local_day_audit": {
            **safety,
            "forecast_valid_local_day_verified": True,
            "market_local_date": "2026-08-24",
            "model_local_date": "2026-08-24",
            "forecast_valid_time": "2026-08-24T12:00:00+00:00",
            "station_timezone": "America/New_York",
            "derived_forecast_local_date": "2026-08-24",
        },
        "anchor_valid_local_day_audit": {
            **safety,
            "anchor_valid_local_day_verified": True,
            "market_local_date": "2026-08-24",
            "independent_anchor_local_date": "2026-08-24",
            "independent_anchor_valid_time": "2026-08-24T12:00:00+00:00",
            "station_timezone": "America/New_York",
            "derived_anchor_local_date": "2026-08-24",
        },
        "model_run_quote_time_audit": {
            **safety,
            "model_run_quote_time_verified": True,
            "model_run_time": "2026-08-24T03:00:00+00:00",
            "quote_observed_at": "2026-08-24T04:00:00+00:00",
        },
    }


def _clean_row():
    row = {
        "blockers": [],
        "exact_market_source_verified": True,
        "settlement_certified_shadow": True,
        "station_identity_verified": True,
        "local_day_verified": True,
        "bracket_semantics_verified": True,
        "rounding_tie_semantics_verified": True,
        "correction_revision_semantics_verified": True,
        "settlement_lifecycle_verified": True,
        "terminal_value_transform": "AS_PUBLISHED",
        "settlement_lifecycle_evidence_url": "https://kalshi.com/markets/kxhighmia-26aug24/miami-high-temperature/kxhighmia-26aug24-b92.5",
        "series_ticker": "KXHIGHMIA",
        "event_ticker": "KXHIGHMIA-26AUG24",
        "market_ticker": "KXHIGHMIA-26AUG24-B92.5",
        "market_date": "2026-08-24",
        "settlement_source_provider": "THE_WEATHER_COMPANY",
        "settlement_source_url": "https://weather.com/kalshi",
        "exact_market_evidence_url": "https://kalshi.com/markets/kxhighmia-26aug24/miami-high-temperature/kxhighmia-26aug24-b92.5",
        "evidence_market_ticker": "KXHIGHMIA-26AUG24-B92.5",
        "evidence_market_date": "2026-08-24",
        "quote_age_ms": 100,
        "max_quote_age_ms": 2000,
        "quote_observed_at": "2026-08-24T04:00:00Z",
        "quote_age_evaluated_at": "2026-08-24T04:00:00.100Z",
        "yes_ask_cents": 40,
        "yes_available_contracts": 5,
        "no_ask_cents": 61,
        "no_available_contracts": 5,
        "best_shadow_side": "NO",
        "modeled_after_fee_ev_cents": "10.0",
        "side_economics": [
            {
                "side": "NO",
                "ask_cents": 61,
                "available_contracts": 5,
                "modeled_after_fee_ev_cents": "10.0",
            },
        ],
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
    row.update(deepcopy(_model_audits()))
    return row


def test_clean_row_clears_read_only_prereqs():
    out = audit_weather_certification_dimensions(_clean_row())
    assert out["blocker_telemetry_verified"] is True
    assert out["exact_market_source_audit_verified"] is True
    assert out["settlement_identity_semantics_verified"] is True
    assert out["model_audit_chain_verified"] is True
    assert out["scored_side_book_binding_verified"] is True
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
    row = _clean_row(); row["quote_age_ms"] = 50; row["quote_age_evaluated_at"] = "2026-08-24T04:00:00.500Z"
    out = audit_weather_certification_dimensions(row)
    assert out["market_data_verified"] is False
    assert out["quote_freshness_binding_verified"] is False


def test_settlement_identity_semantics_require_literal_true():
    row = _clean_row(); row["station_identity_verified"] = "true"
    out = audit_weather_certification_dimensions(row)
    assert out["settlement_identity_semantics_verified"] is False
    assert "STATION_IDENTITY_EVIDENCE_REQUIRED" in out["settlement_blockers"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_exact_market_evidence_must_be_bound_to_event():
    row = _clean_row(); row["exact_market_evidence_url"] = "https://kalshi.com/markets/kxhighny-26aug24/new-york-high-temperature/kxhighny-26aug24-b80.5"
    out = audit_weather_certification_dimensions(row)
    assert out["exact_market_source_audit_verified"] is False
    assert "EXACT_MARKET_EVIDENCE_NOT_BOUND_TO_EVENT" in out["settlement_blockers"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_v2_nested_exact_market_audit_shape_is_replayed_not_trusted():
    row = _clean_row()
    row.pop("settlement_source_provider"); row.pop("settlement_source_url"); row.pop("exact_market_evidence_url")
    row["exact_market_source_audit"] = {
        "verified": True,
        "settlement_source_provider": "THE_WEATHER_COMPANY",
        "settlement_source_url": "https://weather.com/kalshi",
        "evidence_url": "https://kalshi.com/markets/kxhighmia-26aug24/miami-high-temperature/kxhighmia-26aug24-b92.5",
        "evidence_market_ticker": "KXHIGHMIA-26AUG24-B92.5",
        "evidence_market_date": "2026-08-24",
    }
    out = audit_weather_certification_dimensions(row)
    assert out["exact_market_source_audit_verified"] is True
    assert out["settlement_rules_provenance_verified"] is True

    bad = deepcopy(row)
    bad["exact_market_source_audit"]["evidence_url"] = "https://kalshi.com/markets/kxhighny-26aug24/new-york-high-temperature/kxhighny-26aug24-b80.5"
    bad_out = audit_weather_certification_dimensions(bad)
    assert bad_out["exact_market_source_audit_verified"] is False
    assert "EXACT_MARKET_EVIDENCE_NOT_BOUND_TO_EVENT" in bad_out["settlement_blockers"]


def test_model_audit_chain_is_required_not_summary_booleans():
    row = _clean_row(); row.pop("model_sanity_audit")
    out = audit_weather_certification_dimensions(row)
    assert out["model_audit_chain_verified"] is False
    assert out["model_probability_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False


def test_model_chain_rejects_nested_verified_with_blockers():
    row = _clean_row(); row["model_sanity_audit"]["blockers"] = ["MODEL_DISAGREEMENT_RESEARCH_ONLY"]
    out = audit_weather_certification_dimensions(row)
    assert out["model_audit_chain_verified"] is False
    assert "MODEL_AUDIT_ENVELOPE_INVALID:model_sanity_audit" in out["model_blockers"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_model_chain_rejects_calibration_model_mismatch():
    row = _clean_row(); row["model_sanity_audit"]["calibration_model_id"] = "other-model"
    out = audit_weather_certification_dimensions(row)
    assert out["model_audit_chain_verified"] is False
    assert "MODEL_SANITY_CALIBRATION_MODEL_ID_MISMATCH" in out["model_blockers"]


def test_model_chain_rejects_calibration_lookahead():
    row = _clean_row(); row["model_sanity_audit"]["calibration_window_end"] = "2026-08-24T03:30:00+00:00"
    out = audit_weather_certification_dimensions(row)
    assert out["model_audit_chain_verified"] is False
    assert "MODEL_AUDIT_CALIBRATION_LOOKAHEAD_DETECTED" in out["model_blockers"]


def test_model_chain_rejects_anchor_observed_after_quote():
    row = _clean_row(); row["model_sanity_audit"]["independent_anchor_observed_at"] = "2026-08-24T04:00:01+00:00"
    out = audit_weather_certification_dimensions(row)
    assert out["model_audit_chain_verified"] is False
    assert "MODEL_AUDIT_ANCHOR_LOOKAHEAD_DETECTED" in out["model_blockers"]


def test_model_chain_rejects_anchor_provider_overlap():
    row = _clean_row(); row["model_provider_provenance_audit"]["independent_anchor_provider_family"] = "ECMWF"
    out = audit_weather_certification_dimensions(row)
    assert out["model_audit_chain_verified"] is False
    assert "ANCHOR_PROVIDER_NOT_INDEPENDENT" in out["model_blockers"]


def test_scored_side_requires_executable_depth_on_that_side():
    row = _clean_row()
    row.update({
        "best_shadow_side": "NO",
        "modeled_after_fee_ev_cents": "12.5",
        "side_economics": [{
            "side": "NO",
            "ask_cents": 35,
            "available_contracts": 5,
            "modeled_after_fee_ev_cents": "12.5",
        }],
        "yes_ask_cents": 40,
        "yes_available_contracts": 5,
        "no_ask_cents": None,
        "no_available_contracts": None,
    })
    out = audit_weather_certification_dimensions(row)
    assert out["book_representation_verified"] is True  # YES still has a valid book.
    assert out["scored_side_book_binding_verified"] is False
    assert "SCORED_SIDE_EXECUTABLE_BOOK_UNVERIFIED" in out["market_data_blockers"]
    assert out["market_data_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False

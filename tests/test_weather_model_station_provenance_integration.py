from src.weather_settlement_certified_v2 import build_exact_market_certified_shadow_row


def _series():
    return {
        "ticker": "KXHIGHNY",
        "settlement_sources": [{"name": "The Weather Company", "url": "https://weather.com/kalshi"}],
        "contract_terms_url": "https://assets.kalshi.com/contract_terms/GLOBALTEMPERATURE.pdf",
        "fee_type": "quadratic",
        "fee_multiplier": 1,
        "last_updated_ts": "2026-08-18T19:15:56.220522Z",
    }


def _market():
    return {
        "event_ticker": "KXHIGHNY-26AUG21",
        "ticker": "KXHIGHNY-26AUG21-B80.5",
        "market_date": "2026-08-21",
        "local_day_verified": True,
        "bracket_semantics_verified": True,
        "settlement_source_regime_verified": True,
        "settlement_source_provider": "THE_WEATHER_COMPANY",
        "settlement_source_url": "https://weather.com/kalshi",
        "settlement_source_evidence_url": "https://kalshi.com/markets/kxhighny/highest-temperature-in-nyc/kxhighny-26aug21",
        "settlement_source_evidence_market_ticker": "KXHIGHNY-26AUG21-B80.5",
        "settlement_source_evidence_market_date": "2026-08-21",
        "strike_type": "between",
        "floor_strike": 80,
        "cap_strike": 81,
    }


def _quote():
    return {
        "yes_ask_cents": 40,
        "yes_ask_qty": 5,
        "no_ask_cents": 62,
        "no_ask_qty": 5,
        "quote_age_ms": 50,
        "observed_at": "2026-08-21T07:30:00Z",
    }


def _station():
    return {
        "name": "Central Park",
        "station_id": "CLINY",
        "cli_product": "CLINY",
        "identity_verified": True,
    }


def _forecast(include_model_station=True):
    row = {
        "model": "GFS:v1",
        "model_id": "GFS:v1",
        "model_provider": "NOAA",
        "model_component_providers": ["NOAA"],
        "cli_product": "CLINY",
        "model_run_time": "2026-08-21T06:00:00Z",
        "forecast_valid_time": "2026-08-21T23:59:59-04:00",
        "station_mapping_verified": True,
        "freshness_verified": True,
        "bracket_probability": "0.65",
        "model_mean_f": "81.2",
        "independent_anchor_high_f": "81.0",
        "independent_anchor_station_id": "CLINY",
        "independent_anchor_verified": True,
        "independent_anchor_source_id": "TWC:CLINY:daily-high",
        "independent_anchor_provider": "TWC",
        "independent_anchor_model_id": "TWC_DAILY_HIGH:v1",
        "independent_anchor_provenance_url": "https://weather.com/weather/today/l/new-york-ny",
        "independent_anchor_observed_at": "2026-08-21T05:30:00Z",
        "same_local_day_verified": True,
        "disagreement_tolerance_f": "2.0",
        "disagreement_tolerance_audited": True,
        "model_calibration_verified": True,
        "calibration_model_id": "GFS:v1",
        "calibration_target": "DAILY_MAX_TEMPERATURE_F",
        "calibration_evidence_verified": True,
        "calibration_evidence_id": "weather-calibration:CLINY:2026-08-01",
        "calibration_sample_count": 60,
        "calibration_station_id": "CLINY",
        "calibration_window_end": "2026-08-01T23:59:59Z",
    }
    if include_model_station:
        row["model_station_id"] = "CLINY"
    return row


def _build(forecast):
    return build_exact_market_certified_shadow_row(
        series=_series(),
        market=_market(),
        quote=_quote(),
        station=_station(),
        forecast=forecast,
        fee_changes=[],
    )


def test_missing_forecast_model_station_cannot_inherit_settlement_station_for_ev_certification():
    row = _build(_forecast(include_model_station=False))

    assert row["exact_market_source_verified"] is True
    assert row["settlement_certified_shadow"] is True
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert row["best_shadow_side"] is None
    assert row["forecast_bracket_probability"] is None
    assert "FORECAST_STATION_MISMATCH" in row["model_sanity_audit"]["blockers"]
    assert row["economics_status"] == "MODEL_SANITY_UNVERIFIED_RESEARCH_ONLY"
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False


def test_explicit_wrong_forecast_station_cannot_certify_different_settlement_station():
    forecast = _forecast(include_model_station=True)
    forecast["model_station_id"] = "CLIMIA"
    forecast["independent_anchor_station_id"] = "CLIMIA"
    forecast["calibration_station_id"] = "CLIMIA"

    row = _build(forecast)

    assert row["exact_market_source_verified"] is True
    assert row["settlement_certified_shadow"] is True
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert row["best_shadow_side"] is None
    assert row["forecast_bracket_probability"] is None
    assert "FORECAST_SETTLEMENT_STATION_MISMATCH" in row["model_sanity_audit"]["blockers"]
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False


def test_explicit_matching_forecast_model_station_preserves_valid_shadow_path():
    row = _build(_forecast(include_model_station=True))

    assert row["model_sanity_verified"] is True
    assert row["ev_certified_shadow"] is True
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False

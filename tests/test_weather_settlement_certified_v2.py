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


def _market(**overrides):
    row = {
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
    row.update(overrides)
    return row


def _quote(observed_at="2026-08-21T07:30:00Z"):
    return {
        "yes_ask_cents": 40,
        "yes_ask_qty": 5,
        "no_ask_cents": 62,
        "no_ask_qty": 5,
        "quote_age_ms": 50,
        "observed_at": observed_at,
    }


def _station():
    return {
        "name": "Central Park",
        "station_id": "CLINY",
        "cli_product": "CLINY",
        "identity_verified": True,
    }


def _forecast(run_time="2026-08-21T06:00:00Z", valid_time="2026-08-21T23:59:59-04:00", **overrides):
    local_date = run_time[:10]
    row = {
        "model": "GFS:v1",
        "model_id": "GFS:v1",
        "model_provider": "NOAA",
        "model_component_providers": ["NOAA"],
        "cli_product": "CLINY",
        "model_run_time": run_time,
        "forecast_valid_time": valid_time,
        "model_local_date": local_date,
        "independent_anchor_local_date": local_date,
        "station_mapping_verified": True,
        "freshness_verified": True,
        "bracket_probability": "0.65",
        "model_mean_f": "81.2",
        "model_station_id": "CLINY",
        "independent_anchor_high_f": "81.0",
        "independent_anchor_station_id": "CLINY",
        "independent_anchor_verified": True,
        "independent_anchor_source_id": "TWC:CLINY:daily-high",
        "independent_anchor_provider": "TWC",
        "independent_anchor_model_id": "TWC_DAILY_HIGH:v1",
        "independent_anchor_provenance_url": "https://weather.com/weather/today/l/new-york-ny",
        "independent_anchor_observed_at": f"{local_date}T05:30:00Z",
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
    row.update(overrides)
    return row


def _build(market=None, forecast=None):
    return build_exact_market_certified_shadow_row(
        series=_series(),
        market=market or _market(),
        quote=_quote(),
        station=_station(),
        forecast=forecast or _forecast(),
        fee_changes=[],
    )


def test_exact_market_provenance_model_sanity_and_local_day_are_fail_closed_gates():
    row = _build()
    assert row["exact_market_source_verified"] is True
    assert row["local_day_binding_verified"] is True
    assert row["model_sanity_verified"] is True
    assert row["settlement_certified_shadow"] is True
    assert row["ev_certified_shadow"] is True
    assert row["best_shadow_side"] is not None
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False


def test_missing_model_local_date_withholds_ev_but_preserves_settlement_certification():
    row = _build(forecast=_forecast(model_local_date=None))
    assert row["settlement_certified_shadow"] is True
    assert row["local_day_binding_verified"] is False
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert row["best_shadow_side"] is None
    assert row["economics_status"] == "LOCAL_DAY_UNVERIFIED_RESEARCH_ONLY"
    assert "MODEL_LOCAL_DATE_UNVERIFIED" in row["local_day_binding_audit"]["blockers"]


def test_adjacent_day_forecast_cannot_certify_exact_market_ev():
    row = _build(forecast=_forecast(
        model_local_date="2026-08-22",
        independent_anchor_local_date="2026-08-22",
        same_local_day_verified=True,
    ))
    assert row["settlement_certified_shadow"] is True
    assert row["local_day_binding_verified"] is False
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert row["forecast_bracket_probability"] is None
    assert "FORECAST_MARKET_LOCAL_DAY_MISMATCH" in row["local_day_binding_audit"]["blockers"]
    assert "ANCHOR_MARKET_LOCAL_DAY_MISMATCH" in row["local_day_binding_audit"]["blockers"]


def test_stringly_same_local_day_flag_cannot_certify_ev():
    row = _build(forecast=_forecast(same_local_day_verified="true"))
    assert row["local_day_binding_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert "LOCAL_DAY_EQUIVALENCE_UNVERIFIED" in row["local_day_binding_audit"]["blockers"]


def test_verified_historical_nws_exact_market_can_override_current_twc_series_regime_only():
    market = {
        "event_ticker": "KXHIGHNY-26AUG12",
        "ticker": "KXHIGHNY-26AUG12-B80.5",
        "market_date": "2026-08-12",
        "local_day_verified": True,
        "bracket_semantics_verified": True,
        "settlement_source_regime_verified": True,
        "settlement_source_provider": "NWS",
        "settlement_source_url": "https://forecast.weather.gov/product.php?site=NWS&product=CLI&issuedby=NYC",
        "settlement_source_evidence_url": "https://kalshi.com/markets/kxhighny/highest-temperature-in-nyc/kxhighny-26aug12",
        "settlement_source_evidence_market_ticker": "KXHIGHNY-26AUG12-B80.5",
        "settlement_source_evidence_market_date": "2026-08-12",
        "strike_type": "between",
        "floor_strike": 80,
        "cap_strike": 81,
    }
    row = build_exact_market_certified_shadow_row(
        series=_series(),
        market=market,
        quote=_quote(observed_at="2026-08-12T12:00:00Z"),
        station=_station(),
        forecast=_forecast(
            run_time="2026-08-12T06:00:00Z",
            valid_time="2026-08-12T23:59:59-04:00",
            model_local_date="2026-08-12",
            independent_anchor_local_date="2026-08-12",
            independent_anchor_observed_at="2026-08-12T05:30:00Z",
            calibration_window_end="2026-08-01T23:59:59Z",
        ),
        fee_changes=[{
            "series_ticker": "KXHIGHNY",
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "scheduled_ts": "2026-01-01T00:00:00Z",
        }],
        fee_history_complete=True,
    )
    assert row["exact_market_source_verified"] is True
    assert row["local_day_binding_verified"] is True
    assert row["model_sanity_verified"] is True
    assert "EXACT_MARKET_SETTLEMENT_REGIME_UNVERIFIED" not in row["blockers"]
    assert row["settlement_certified_shadow"] is True
    assert row["ev_certified_shadow"] is True
    assert row["live_probe_ready"] is False


def test_missing_exact_source_url_quarantines_all_ev_economics():
    market = _market()
    market.pop("settlement_source_url")
    row = _build(market)
    assert row["exact_market_source_verified"] is False
    assert row["settlement_certified_shadow"] is False
    assert row["ev_certified_shadow"] is False
    assert row["best_shadow_side"] is None
    assert row["side_economics"] == []
    assert row["modeled_after_fee_ev_cents"] is None
    assert row["economics_status"] == "WITHHELD_EXACT_MARKET_SOURCE_UNVERIFIED"
    assert "EXACT_MARKET_SOURCE_AUDIT_REQUIRED" in row["blockers"]


def test_generic_series_page_cannot_certify_exact_market():
    row = _build(_market(settlement_source_evidence_url="https://kalshi.com/markets/kxhighny"))
    assert row["exact_market_source_verified"] is False
    assert "EXACT_MARKET_EVIDENCE_NOT_BOUND_TO_EVENT" in row["blockers"]
    assert row["side_economics"] == []


def test_exact_audit_never_upgrades_underlying_failed_quote_gate():
    quote = _quote()
    quote["quote_age_ms"] = 5000
    row = build_exact_market_certified_shadow_row(
        series=_series(), market=_market(), quote=quote, station=_station(),
        forecast=_forecast(), fee_changes=[]
    )
    assert row["exact_market_source_verified"] is True
    assert row["settlement_certified_shadow"] is False
    assert row["ev_certified_shadow"] is False
    assert "QUOTE_STALE" in row["blockers"]
    assert row["live_probe_ready"] is False


def test_large_model_anchor_disagreement_keeps_settlement_certified_but_withholds_ev():
    row = _build(forecast=_forecast(
        model_mean_f="88.8",
        independent_anchor_high_f="92.0",
        disagreement_tolerance_f="2.0",
    ))
    assert row["exact_market_source_verified"] is True
    assert row["settlement_certified_shadow"] is True
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert row["best_shadow_side"] is None
    assert row["forecast_bracket_probability"] is None
    assert row["economics_status"] == "MODEL_SANITY_UNVERIFIED_RESEARCH_ONLY"
    assert "MODEL_ANCHOR_DISAGREEMENT_EXCEEDS_AUDITED_TOLERANCE" in row["model_sanity_audit"]["blockers"]
    assert all(side["modeled_after_fee_ev_cents"] is None for side in row["side_economics"])
    assert row["live_probe_ready"] is False


def test_missing_model_sanity_evidence_cannot_certify_ev():
    row = _build(forecast={
        "model": "GFS:v1",
        "cli_product": "CLINY",
        "model_run_time": "2026-08-21T06:00:00Z",
        "forecast_valid_time": "2026-08-21T23:59:59-04:00",
        "model_local_date": "2026-08-21",
        "independent_anchor_local_date": "2026-08-21",
        "same_local_day_verified": True,
        "station_mapping_verified": True,
        "freshness_verified": True,
        "bracket_probability": "0.65",
    })
    assert row["settlement_certified_shadow"] is True
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert row["best_shadow_side"] is None
    assert row["forecast_bracket_probability"] is None
    assert row["live_probe_ready"] is False


def test_future_calibration_window_cannot_certify_current_forecast():
    row = _build(forecast=_forecast(
        calibration_window_end="2026-08-22T00:00:00Z",
        run_time="2026-08-21T06:00:00Z",
    ))
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert "MODEL_CALIBRATION_LOOKAHEAD_DETECTED" in row["model_sanity_audit"]["blockers"]


def test_other_models_calibration_cannot_certify_gfs_ev():
    row = _build(forecast=_forecast(calibration_model_id="ECMWF:v1"))
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert "MODEL_CALIBRATION_MODEL_ID_MISMATCH" in row["model_sanity_audit"]["blockers"]


def test_anchor_update_observed_after_quote_cannot_certify_prior_edge():
    row = _build(forecast=_forecast(independent_anchor_observed_at="2026-08-21T08:00:00Z"))
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert "INDEPENDENT_FORECAST_ANCHOR_LOOKAHEAD_DETECTED" in row["model_sanity_audit"]["blockers"]


def test_arbitrary_anchor_source_string_cannot_certify_ev():
    row = _build(forecast=_forecast(
        independent_anchor_source_id="independent-trust-me",
        independent_anchor_provenance_url=None,
    ))
    assert row["settlement_certified_shadow"] is True
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert "INDEPENDENT_FORECAST_ANCHOR_PROVENANCE_INVALID" in row["model_sanity_audit"]["blockers"]


def test_same_provider_derivative_anchor_cannot_certify_ev():
    row = _build(forecast=_forecast(
        independent_anchor_provider="NWS",
        independent_anchor_model_id="NWS_GRIDPOINT:v1",
        independent_anchor_provenance_url="https://forecast.weather.gov/MapClick.php?lat=40.7&lon=-74.0",
    ))
    assert row["settlement_certified_shadow"] is True
    assert row["model_sanity_verified"] is False
    assert row["ev_certified_shadow"] is False
    assert "INDEPENDENT_FORECAST_ANCHOR_PROVIDER_NOT_INDEPENDENT" in row["model_sanity_audit"]["blockers"]

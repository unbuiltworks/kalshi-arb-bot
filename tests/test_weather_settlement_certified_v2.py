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
    return {"name": "Central Park", "cli_product": "CLINY", "identity_verified": True}


def _forecast():
    return {
        "model": "GFS",
        "cli_product": "CLINY",
        "model_run_time": "2026-08-21T06:00:00Z",
        "forecast_valid_time": "2026-08-21T23:59:59-04:00",
        "station_mapping_verified": True,
        "freshness_verified": True,
        "bracket_probability": "0.65",
    }


def _build(market=None):
    return build_exact_market_certified_shadow_row(
        series=_series(),
        market=market or _market(),
        quote=_quote(),
        station=_station(),
        forecast=_forecast(),
        fee_changes=[],
    )


def test_exact_market_provenance_is_additional_fail_closed_gate():
    row = _build()
    assert row["exact_market_source_verified"] is True
    assert row["settlement_certified_shadow"] is True
    assert row["ev_certified_shadow"] is True
    assert row["best_shadow_side"] is not None
    assert row["order_entry"] is False
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
    row = _build(_market(
        settlement_source_evidence_url="https://kalshi.com/markets/kxhighny",
    ))
    assert row["exact_market_source_verified"] is False
    assert "EXACT_MARKET_EVIDENCE_NOT_BOUND_TO_EVENT" in row["blockers"]
    assert row["side_economics"] == []


def test_evidence_ticker_or_date_mismatch_quarantines_shadow_economics():
    row = _build(_market(
        settlement_source_evidence_market_ticker="KXHIGHNY-26AUG21-B82.5",
        settlement_source_evidence_market_date="2026-08-20",
    ))
    assert row["exact_market_source_verified"] is False
    assert "EXACT_MARKET_EVIDENCE_MISMATCH" in row["blockers"]
    assert row["ev_certified_shadow"] is False


def test_exact_audit_never_upgrades_underlying_failed_quote_gate():
    quote = _quote()
    quote["quote_age_ms"] = 5000
    row = build_exact_market_certified_shadow_row(
        series=_series(),
        market=_market(),
        quote=quote,
        station=_station(),
        forecast=_forecast(),
        fee_changes=[],
    )
    assert row["exact_market_source_verified"] is True
    assert row["settlement_certified_shadow"] is False
    assert row["ev_certified_shadow"] is False
    assert "QUOTE_STALE" in row["blockers"]
    assert row["live_probe_ready"] is False

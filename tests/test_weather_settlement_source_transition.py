from src.weather_settlement_certified import build_shadow_row, certify_series


def _current_series():
    return {
        "ticker": "KXHIGHNY",
        "settlement_sources": [
            {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
        ],
        "contract_terms_url": "https://assets.kalshi.com/contract_terms/GLOBALTEMPERATURE.pdf",
        "fee_type": "quadratic",
        "fee_multiplier": 1,
        "last_updated_ts": "2026-08-18T19:15:56.220522Z",
    }


def _current_market():
    return {
        "event_ticker": "KXHIGHNY-26AUG20",
        "ticker": "KXHIGHNY-26AUG20-B80.5",
        "market_date": "2026-08-20",
        "local_day_verified": True,
        "bracket_semantics_verified": True,
        "settlement_source_regime_verified": True,
        "settlement_source_provider": "THE_WEATHER_COMPANY",
        "settlement_source_evidence_url": "https://kalshi.com/markets/kxhighny/kxhighny-26aug20",
        "strike_type": "between",
        "floor_strike": 80,
        "cap_strike": 81,
    }


def test_current_weather_company_source_is_certified_from_series_metadata():
    ok, blockers = certify_series(_current_series())
    assert ok is True
    assert blockers == []


def test_weather_company_source_requires_exact_kalshi_endpoint():
    series = _current_series()
    series["settlement_sources"] = [
        {"name": "The Weather Company", "url": "https://weather.com/weather/today"}
    ]
    ok, blockers = certify_series(series)
    assert ok is False
    assert "SETTLEMENT_SOURCE_UNVERIFIED" in blockers


def test_current_provider_is_exposed_without_enabling_live_readiness():
    row = build_shadow_row(
        series=_current_series(),
        market=_current_market(),
        quote={
            "yes_ask_cents": 40,
            "yes_ask_qty": 5,
            "quote_age_ms": 100,
            "observed_at": "2026-08-20T16:00:00Z",
        },
        station={"name": "Central Park", "cli_product": "CLINY", "identity_verified": True},
        forecast=None,
        fee_changes=[],
    )
    assert row["settlement_certified_shadow"] is True
    assert row["settlement_source_providers"] == ["THE_WEATHER_COMPANY"]
    assert row["market_settlement_source_provider"] == "THE_WEATHER_COMPANY"
    assert row["settlement_source_regime_verified"] is True
    assert row["fee_schedule_checked"] is True
    assert row["ev_certified_shadow"] is False
    assert row["live_probe_ready"] is False


def test_exact_market_regime_is_required_even_when_current_series_source_is_valid():
    market = _current_market()
    market.pop("settlement_source_regime_verified")
    row = build_shadow_row(
        series=_current_series(),
        market=market,
        quote={"yes_ask_cents": 40, "yes_ask_qty": 5, "quote_age_ms": 100, "observed_at": "2026-08-20T16:00:00Z"},
        station={"name": "Central Park", "cli_product": "CLINY", "identity_verified": True},
        forecast=None,
        fee_changes=[],
    )
    assert row["settlement_certified_shadow"] is False
    assert "EXACT_MARKET_SETTLEMENT_REGIME_UNVERIFIED" in row["blockers"]


def test_market_provider_must_match_series_regime():
    market = _current_market()
    market["settlement_source_provider"] = "NWS"
    row = build_shadow_row(
        series=_current_series(),
        market=market,
        quote={"yes_ask_cents": 40, "yes_ask_qty": 5, "quote_age_ms": 100, "observed_at": "2026-08-20T16:00:00Z"},
        station={"name": "Central Park", "cli_product": "CLINY", "identity_verified": True},
        forecast=None,
        fee_changes=[],
    )
    assert row["settlement_certified_shadow"] is False
    assert "EXACT_MARKET_SETTLEMENT_REGIME_UNVERIFIED" in row["blockers"]


def test_third_party_weather_company_lookalike_fails_closed():
    series = _current_series()
    series["settlement_sources"] = [
        {"name": "The Weather Company", "url": "https://weather.com.example.org/kalshi"}
    ]
    ok, blockers = certify_series(series)
    assert ok is False
    assert "SETTLEMENT_SOURCE_UNVERIFIED" in blockers

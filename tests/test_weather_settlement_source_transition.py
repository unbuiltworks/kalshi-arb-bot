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
        market={
            "event_ticker": "KXHIGHNY-26AUG20",
            "ticker": "KXHIGHNY-26AUG20-B80.5",
            "market_date": "2026-08-20",
            "local_day_verified": True,
            "bracket_semantics_verified": True,
            "strike_type": "between",
            "floor_strike": 80,
            "cap_strike": 81,
        },
        quote={"yes_ask_cents": 40, "yes_ask_qty": 5, "quote_age_ms": 100},
        station={"name": "Central Park", "cli_product": "CLINY", "identity_verified": True},
        forecast=None,
    )
    assert row["settlement_certified_shadow"] is True
    assert row["settlement_source_providers"] == ["THE_WEATHER_COMPANY"]
    assert row["ev_certified_shadow"] is False
    assert row["live_probe_ready"] is False


def test_third_party_weather_company_lookalike_fails_closed():
    series = _current_series()
    series["settlement_sources"] = [
        {"name": "The Weather Company", "url": "https://weather.com.example.org/kalshi"}
    ]
    ok, blockers = certify_series(series)
    assert ok is False
    assert "SETTLEMENT_SOURCE_UNVERIFIED" in blockers

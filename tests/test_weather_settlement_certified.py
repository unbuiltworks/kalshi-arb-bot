from decimal import Decimal

from src.weather_settlement_certified import _fee_cents, build_shadow_row, certify_series


def _series():
    return {
        "ticker": "KXHIGHTDC",
        "settlement_sources": [{"name": "NWS", "url": "https://www.weather.gov/wrh/Climate?wfo=lwx"}],
        "contract_terms_url": "https://kalshi.com/markets/kxhightdc",
        "fee_type": "quadratic",
        "fee_multiplier": 1,
    }


def _market():
    return {
        "event_ticker": "KXHIGHTDC-26AUG20",
        "ticker": "KXHIGHTDC-26AUG20-B91.5",
        "market_date": "2026-08-20",
        "local_day_verified": True,
        "bracket_semantics_verified": True,
        "strike_type": "between",
        "floor_strike": 90,
        "cap_strike": 91,
    }


def test_series_requires_official_nws_source_and_current_fee_metadata():
    ok, blockers = certify_series(_series())
    assert ok is True
    assert blockers == []
    bad = _series()
    bad["settlement_sources"] = [{"name": "blog", "url": "https://example.com/weather"}]
    ok, blockers = certify_series(bad)
    assert ok is False
    assert "SETTLEMENT_SOURCE_UNVERIFIED" in blockers


def test_fee_multiplier_is_multiplier_not_rate():
    # q=10 @ 50c: 0.07*1*10*.5*.5 = $0.175 => 18 cents after ceil-to-cent.
    assert _fee_cents(10, 50, "quadratic", 1) == 18


def test_shadow_row_is_read_only_and_computes_after_fee_ev():
    row = build_shadow_row(
        series=_series(), market=_market(),
        quote={"ask_cents": 40, "ask_qty": 5, "quote_age_ms": 120},
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast={"bracket_probability": "0.55"},
    )
    assert row["category"] == "weather"
    assert row["classification"] == "SHADOW"
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False
    assert row["settlement_certified_shadow"] is True
    assert row["modeled_taker_fee_cents"] == 9
    assert Decimal(row["modeled_after_fee_ev_cents"]) == Decimal("66.00")


def test_missing_station_and_semantics_fail_closed():
    market = _market()
    market["local_day_verified"] = False
    market["bracket_semantics_verified"] = False
    row = build_shadow_row(
        series=_series(), market=market,
        quote={"ask_cents": 40, "ask_qty": 5, "quote_age_ms": 120},
        station={"name": "Washington", "cli_product": "", "identity_verified": False},
    )
    assert row["settlement_certified_shadow"] is False
    assert "STATION_IDENTITY_UNVERIFIED" in row["blockers"]
    assert "LOCAL_DAY_UNVERIFIED" in row["blockers"]
    assert "BRACKET_SEMANTICS_UNVERIFIED" in row["blockers"]


def test_unknown_fee_type_fails_closed():
    series = _series()
    series["fee_type"] = "unknown"
    row = build_shadow_row(
        series=series, market=_market(),
        quote={"ask_cents": 40, "ask_qty": 5, "quote_age_ms": 120},
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
    )
    assert "FEE_UNVERIFIED" in row["blockers"]
    assert row["live_probe_ready"] is False

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
    assert row["best_shadow_side"]["side"] == "YES"


def test_models_yes_and_no_executable_asks_independently():
    row = build_shadow_row(
        series=_series(), market=_market(),
        quote={
            "yes_ask_cents": 60, "yes_ask_qty": 5,
            "no_ask_cents": 35, "no_ask_qty": 4,
            "quote_age_ms": 100,
        },
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast={"bracket_probability": "0.40"},
    )
    assert row["settlement_certified_shadow"] is True
    assert [x["side"] for x in row["side_economics"]] == ["YES", "NO"]
    yes = row["side_economics"][0]
    no = row["side_economics"][1]
    assert Decimal(yes["forecast_side_probability"]) == Decimal("0.40")
    assert Decimal(no["forecast_side_probability"]) == Decimal("0.60")
    assert row["best_shadow_side"]["side"] == "NO"
    assert Decimal(no["modeled_after_fee_ev_cents"]) > 0


def test_no_only_book_is_valid_and_does_not_fabricate_yes_aliases():
    row = build_shadow_row(
        series=_series(), market=_market(),
        quote={"no_ask_cents": 42, "no_ask_qty": 3, "quote_age_ms": 80},
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast={"bracket_probability": "0.30"},
    )
    assert row["settlement_certified_shadow"] is True
    assert row["ask_cents"] is None
    assert row["available_contracts"] is None
    assert row["modeled_taker_fee_cents"] is None
    assert row["side_economics"][0]["side"] == "NO"
    assert row["best_shadow_side"]["side"] == "NO"


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


def test_verification_flags_require_literal_true():
    market = _market()
    market["local_day_verified"] = "false"
    market["bracket_semantics_verified"] = "true"
    row = build_shadow_row(
        series=_series(), market=market,
        quote={"ask_cents": 40, "ask_qty": 5, "quote_age_ms": 120},
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": "true"},
    )
    assert row["settlement_certified_shadow"] is False
    assert "STATION_IDENTITY_UNVERIFIED" in row["blockers"]
    assert "LOCAL_DAY_UNVERIFIED" in row["blockers"]
    assert "BRACKET_SEMANTICS_UNVERIFIED" in row["blockers"]


def test_stale_quote_is_not_settlement_certified_shadow_economics():
    row = build_shadow_row(
        series=_series(), market=_market(),
        quote={"ask_cents": 40, "ask_qty": 5, "quote_age_ms": 2501},
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast={"bracket_probability": "0.55"},
        max_quote_age_ms=2000,
    )
    assert row["settlement_certified_shadow"] is False
    assert "QUOTE_STALE" in row["blockers"]
    assert row["live_probe_ready"] is False


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

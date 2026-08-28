from decimal import Decimal

from src.weather_settlement_certified import _fee_cents, build_shadow_row, certify_series


def _series():
    return {
        "ticker": "KXHIGHTDC",
        "settlement_sources": [{"name": "NWS", "url": "https://www.weather.gov/wrh/Climate?wfo=lwx"}],
        "contract_terms_url": "https://kalshi.com/markets/kxhightdc",
        "fee_type": "quadratic",
        "fee_multiplier": 1,
        "last_updated_ts": "2026-08-20T10:00:00Z",
    }


def _market():
    return {
        "event_ticker": "KXHIGHTDC-26AUG20",
        "ticker": "KXHIGHTDC-26AUG20-B91.5",
        "market_date": "2026-08-20",
        "local_day_verified": True,
        "bracket_semantics_verified": True,
        "settlement_source_regime_verified": True,
        "settlement_source_provider": "NWS",
        "settlement_source_evidence_url": "https://kalshi.com/markets/kxhightdc/kxhightdc-26aug20",
        "strike_type": "between",
        "floor_strike": 90,
        "cap_strike": 91,
    }


def _forecast(probability="0.55"):
    return {
        "model": "GFS",
        "cli_product": "CLIDCA",
        "model_run_time": "2026-08-20T12:00:00Z",
        "forecast_valid_time": "2026-08-20T23:59:59-04:00",
        "station_mapping_verified": True,
        "freshness_verified": True,
        "bracket_probability": probability,
    }


def _quote(**overrides):
    quote = {"ask_cents": 40, "ask_qty": 5, "quote_age_ms": 120, "observed_at": "2026-08-20T16:00:00Z"}
    quote.update(overrides)
    return quote


def _build(**kwargs):
    kwargs.setdefault("fee_changes", [])
    return build_shadow_row(**kwargs)


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
    assert _fee_cents(10, 50, "quadratic", 1) == 18


def test_shadow_row_is_read_only_and_computes_after_fee_ev():
    row = _build(
        series=_series(), market=_market(), quote=_quote(),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=_forecast(),
    )
    assert row["category"] == "weather"
    assert row["classification"] == "SHADOW"
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False
    assert row["settlement_certified_shadow"] is True
    assert row["ev_certified_shadow"] is True
    assert row["forecast_cli_product"] == "CLIDCA"
    assert row["modeled_taker_fee_cents"] == 9
    assert Decimal(row["modeled_after_fee_ev_cents"]) == Decimal("66.00")
    assert row["best_shadow_side"]["side"] == "YES"
    assert row["fee_schedule_checked"] is True
    assert row["fee_metadata_source"] == "series_current"


def test_models_yes_and_no_executable_asks_independently():
    row = _build(
        series=_series(), market=_market(),
        quote=_quote(yes_ask_cents=60, yes_ask_qty=5, no_ask_cents=35, no_ask_qty=4),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=_forecast("0.40"),
    )
    assert row["settlement_certified_shadow"] is True
    assert row["ev_certified_shadow"] is True
    assert [x["side"] for x in row["side_economics"]] == ["YES", "NO"]
    yes = row["side_economics"][0]
    no = row["side_economics"][1]
    assert Decimal(yes["forecast_side_probability"]) == Decimal("0.40")
    assert Decimal(no["forecast_side_probability"]) == Decimal("0.60")
    assert row["best_shadow_side"]["side"] == "NO"
    assert Decimal(no["modeled_after_fee_ev_cents"]) > 0


def test_no_only_book_is_valid_and_does_not_fabricate_yes_aliases():
    row = _build(
        series=_series(), market=_market(),
        quote={"no_ask_cents": 42, "no_ask_qty": 3, "quote_age_ms": 80, "observed_at": "2026-08-20T16:00:00Z"},
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=_forecast("0.30"),
    )
    assert row["settlement_certified_shadow"] is True
    assert row["ev_certified_shadow"] is True
    assert row["ask_cents"] is None
    assert row["available_contracts"] is None
    assert row["modeled_taker_fee_cents"] is None
    assert row["side_economics"][0]["side"] == "NO"
    assert row["best_shadow_side"]["side"] == "NO"


def test_missing_station_and_semantics_fail_closed():
    market = _market()
    market["local_day_verified"] = False
    market["bracket_semantics_verified"] = False
    row = _build(
        series=_series(), market=market, quote=_quote(),
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
    row = _build(
        series=_series(), market=market, quote=_quote(),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": "true"},
    )
    assert row["settlement_certified_shadow"] is False
    assert "STATION_IDENTITY_UNVERIFIED" in row["blockers"]
    assert "LOCAL_DAY_UNVERIFIED" in row["blockers"]
    assert "BRACKET_SEMANTICS_UNVERIFIED" in row["blockers"]


def test_stale_quote_is_not_settlement_certified_shadow_economics():
    row = _build(
        series=_series(), market=_market(), quote=_quote(quote_age_ms=2501),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=_forecast(), max_quote_age_ms=2000,
    )
    assert row["settlement_certified_shadow"] is False
    assert row["ev_certified_shadow"] is False
    assert "QUOTE_STALE" in row["blockers"]
    assert row["live_probe_ready"] is False


def test_unknown_fee_type_fails_closed():
    series = _series()
    series["fee_type"] = "unknown"
    row = _build(
        series=series, market=_market(), quote=_quote(),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
    )
    assert "FEE_UNVERIFIED" in row["blockers"]
    assert row["live_probe_ready"] is False


def test_missing_fee_schedule_or_quote_timestamp_fails_closed():
    row = build_shadow_row(
        series=_series(), market=_market(),
        quote={"ask_cents": 40, "ask_qty": 5, "quote_age_ms": 120},
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=_forecast(), fee_changes=None,
    )
    assert row["settlement_certified_shadow"] is False
    assert row["ev_certified_shadow"] is False
    assert row["fee_schedule_checked"] is False
    assert "FEE_SCHEDULE_UNVERIFIED" in row["blockers"]
    assert row["side_economics"] == []


def test_scheduled_fee_change_effective_at_quote_time_is_applied():
    series = _series()
    fee_changes = [{
        "id": "change-1",
        "series_ticker": "KXHIGHTDC",
        "fee_type": "quadratic",
        "fee_multiplier": 2,
        "scheduled_ts": "2026-08-20T15:00:00Z",
    }]
    row = _build(
        series=series, market=_market(), quote=_quote(ask_cents=50, ask_qty=10),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=_forecast("0.70"), fee_changes=fee_changes,
    )
    assert row["settlement_certified_shadow"] is True
    assert row["fee_schedule_checked"] is True
    assert row["fee_metadata_source"] == "scheduled_fee_change"
    assert row["fee_multiplier"] == 2
    assert row["modeled_taker_fee_cents"] == 35


def test_future_fee_change_does_not_change_current_quote_fee():
    fee_changes = [{
        "id": "future",
        "series_ticker": "KXHIGHTDC",
        "fee_type": "quadratic",
        "fee_multiplier": 3,
        "scheduled_ts": "2026-08-21T00:00:00Z",
    }]
    row = _build(
        series=_series(), market=_market(), quote=_quote(ask_cents=50, ask_qty=10),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=_forecast("0.70"), fee_changes=fee_changes,
    )
    assert row["fee_metadata_source"] == "series_current"
    assert row["fee_multiplier"] == 1
    assert row["modeled_taker_fee_cents"] == 18


def test_malformed_matching_fee_change_fails_closed():
    fee_changes = [{
        "id": "bad",
        "series_ticker": "KXHIGHTDC",
        "fee_type": "quadratic",
        "fee_multiplier": 2,
        "scheduled_ts": "not-a-time",
    }]
    row = _build(
        series=_series(), market=_market(), quote=_quote(),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=_forecast(), fee_changes=fee_changes,
    )
    assert row["settlement_certified_shadow"] is False
    assert "FEE_SCHEDULE_UNVERIFIED" in row["blockers"]
    assert row["side_economics"] == []


def test_naked_forecast_probability_cannot_be_ranked_as_ev():
    row = _build(
        series=_series(), market=_market(),
        quote=_quote(yes_ask_cents=35, yes_ask_qty=10),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast={"bracket_probability": "0.80"},
    )
    assert row["settlement_certified_shadow"] is True
    assert row["ev_certified_shadow"] is False
    assert row["forecast_bracket_probability"] is None
    assert row["best_shadow_side"] is None
    assert row["modeled_after_fee_ev_cents"] is None
    assert "FORECAST_UNVERIFIED" in row["forecast_blockers"]
    assert "FORECAST_STATION_MAPPING_UNVERIFIED" in row["forecast_blockers"]
    assert "FORECAST_STALE_OR_UNVERIFIED" in row["forecast_blockers"]
    assert "FORECAST_STATION_ID_MISMATCH" in row["forecast_blockers"]
    assert "FORECAST_TIME_UNVERIFIED" in row["forecast_blockers"]


def test_string_forecast_verification_flags_fail_closed():
    forecast = _forecast()
    forecast["station_mapping_verified"] = "true"
    forecast["freshness_verified"] = "true"
    row = _build(
        series=_series(), market=_market(), quote=_quote(yes_ask_cents=35, yes_ask_qty=10),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=forecast,
    )
    assert row["settlement_certified_shadow"] is True
    assert row["ev_certified_shadow"] is False
    assert row["best_shadow_side"] is None
    assert "FORECAST_STATION_MAPPING_UNVERIFIED" in row["forecast_blockers"]
    assert "FORECAST_STALE_OR_UNVERIFIED" in row["forecast_blockers"]


def test_wrong_forecast_station_fails_even_when_mapping_boolean_is_true():
    forecast = _forecast("0.90")
    forecast["cli_product"] = "CLIATL"
    row = _build(
        series=_series(), market=_market(), quote=_quote(yes_ask_cents=20, yes_ask_qty=10),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=forecast,
    )
    assert row["settlement_certified_shadow"] is True
    assert row["ev_certified_shadow"] is False
    assert row["forecast_bracket_probability"] is None
    assert row["best_shadow_side"] is None
    assert "FORECAST_STATION_ID_MISMATCH" in row["forecast_blockers"]


def test_malformed_or_naive_forecast_timestamps_fail_closed():
    for run_time, valid_time in [
        ("not-a-time", "2026-08-20T23:59:59-04:00"),
        ("2026-08-20T12:00:00", "2026-08-20T23:59:59-04:00"),
        ("2026-08-21T12:00:00Z", "2026-08-20T23:59:59-04:00"),
    ]:
        forecast = _forecast("0.75")
        forecast["model_run_time"] = run_time
        forecast["forecast_valid_time"] = valid_time
        row = _build(
            series=_series(), market=_market(), quote=_quote(yes_ask_cents=25, yes_ask_qty=8, quote_age_ms=50),
            station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
            forecast=forecast,
        )
        assert row["settlement_certified_shadow"] is True
        assert row["ev_certified_shadow"] is False
        assert row["best_shadow_side"] is None
        assert "FORECAST_TIME_UNVERIFIED" in row["forecast_blockers"]


def test_exact_market_identity_and_regime_fail_closed():
    market = _market()
    market["ticker"] = "KXHIGHMIA-26AUG20-B91.5"
    market["settlement_source_regime_verified"] = "true"
    row = _build(
        series=_series(), market=market, quote=_quote(),
        station={"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True},
        forecast=_forecast(),
    )
    assert row["settlement_certified_shadow"] is False
    assert "EXACT_MARKET_IDENTITY_UNVERIFIED" in row["blockers"]
    assert "EXACT_MARKET_SETTLEMENT_REGIME_UNVERIFIED" in row["blockers"]

from decimal import Decimal

from src.weather_settlement_certified import build_shadow_row


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
        "strike_type": "between",
        "floor_strike": 90,
        "cap_strike": 91,
    }


def _station():
    return {"name": "Washington National", "cli_product": "CLIDCA", "identity_verified": True}


def _forecast():
    return {
        "model": "GFS",
        "cli_product": "CLIDCA",
        "model_run_time": "2026-08-20T06:00:00Z",
        "forecast_valid_time": "2026-08-20T23:59:59-04:00",
        "station_mapping_verified": True,
        "freshness_verified": True,
        "bracket_probability": "0.70",
    }


def _historical_quote():
    return {
        "ask_cents": 50,
        "ask_qty": 10,
        "quote_age_ms": 50,
        "observed_at": "2026-08-20T09:30:00Z",
    }


def _historical_fee_change():
    return [{
        "id": "historical-effective",
        "series_ticker": "KXHIGHTDC",
        "fee_type": "quadratic",
        "fee_multiplier": 2,
        "scheduled_ts": "2026-08-20T09:00:00Z",
    }]


def test_backdated_quote_cannot_inherit_current_series_fee_without_complete_history():
    row = build_shadow_row(
        series=_series(),
        market=_market(),
        quote=_historical_quote(),
        station=_station(),
        forecast=_forecast(),
        fee_changes=_historical_fee_change(),
        fee_history_complete=False,
    )

    assert row["settlement_certified_shadow"] is False
    assert row["ev_certified_shadow"] is False
    assert row["side_economics"] == []
    assert row["fee_metadata_source"] is None
    assert row["fee_schedule_checked"] is False
    assert "FEE_HISTORY_UNVERIFIED" in row["blockers"]


def test_backdated_quote_uses_effective_historical_fee_only_when_history_is_complete():
    row = build_shadow_row(
        series=_series(),
        market=_market(),
        quote=_historical_quote(),
        station=_station(),
        forecast=_forecast(),
        fee_changes=_historical_fee_change(),
        fee_history_complete=True,
    )

    assert row["settlement_certified_shadow"] is True
    assert row["ev_certified_shadow"] is True
    assert row["fee_metadata_source"] == "historical_fee_change"
    assert row["fee_multiplier"] == 2
    assert row["fee_schedule_checked"] is True
    assert row["fee_history_complete"] is True
    assert row["modeled_taker_fee_cents"] == 35
    assert Decimal(row["modeled_after_fee_ev_cents"]) == Decimal("165.00")


def test_string_true_does_not_certify_historical_fee_completeness():
    row = build_shadow_row(
        series=_series(),
        market=_market(),
        quote=_historical_quote(),
        station=_station(),
        forecast=_forecast(),
        fee_changes=_historical_fee_change(),
        fee_history_complete="true",  # type: ignore[arg-type]
    )

    assert row["settlement_certified_shadow"] is False
    assert row["fee_history_complete"] is False
    assert "FEE_HISTORY_UNVERIFIED" in row["blockers"]

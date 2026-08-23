from src.weather_forecast_valid_local_day_guard import audit_forecast_valid_local_day


def _audit(**overrides):
    params = {
        "market_date": "2026-08-21",
        "model_local_date": "2026-08-21",
        "forecast_valid_time": "2026-08-22T03:59:59Z",
        "station_timezone": "America/New_York",
        "timezone_mapping_verified": True,
    }
    params.update(overrides)
    return audit_forecast_valid_local_day(**params)


def test_valid_time_is_bound_to_exact_market_day_in_station_timezone():
    row = _audit()
    assert row["forecast_valid_local_day_verified"] is True
    assert row["derived_forecast_local_date"] == "2026-08-21"
    assert row["blockers"] == []
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False


def test_same_utc_date_but_wrong_station_local_day_fails_closed():
    row = _audit(forecast_valid_time="2026-08-22T04:00:00Z")
    assert row["forecast_valid_local_day_verified"] is False
    assert row["derived_forecast_local_date"] == "2026-08-22"
    assert "FORECAST_VALID_TIME_MARKET_DAY_MISMATCH" in row["blockers"]
    assert "FORECAST_VALID_TIME_MODEL_DAY_MISMATCH" in row["blockers"]


def test_stringly_timezone_verification_cannot_certify():
    row = _audit(timezone_mapping_verified="true")
    assert row["forecast_valid_local_day_verified"] is False
    assert "STATION_TIMEZONE_MAPPING_UNVERIFIED" in row["blockers"]


def test_missing_or_invalid_timezone_cannot_certify():
    missing = _audit(station_timezone=None)
    assert missing["forecast_valid_local_day_verified"] is False
    assert "STATION_TIMEZONE_UNVERIFIED" in missing["blockers"]

    invalid = _audit(station_timezone="Mars/Olympus_Mons")
    assert invalid["forecast_valid_local_day_verified"] is False
    assert "STATION_TIMEZONE_INVALID" in invalid["blockers"]


def test_naive_or_malformed_valid_timestamp_cannot_certify():
    naive = _audit(forecast_valid_time="2026-08-21T23:59:59")
    assert naive["forecast_valid_local_day_verified"] is False
    assert "FORECAST_VALID_TIME_UNVERIFIED" in naive["blockers"]

    malformed = _audit(forecast_valid_time="not-a-time")
    assert malformed["forecast_valid_local_day_verified"] is False
    assert "FORECAST_VALID_TIME_UNVERIFIED" in malformed["blockers"]


def test_declared_model_day_cannot_disagree_with_derived_local_day():
    row = _audit(model_local_date="2026-08-22")
    assert row["forecast_valid_local_day_verified"] is False
    assert row["derived_forecast_local_date"] == "2026-08-21"
    assert "FORECAST_VALID_TIME_MODEL_DAY_MISMATCH" in row["blockers"]

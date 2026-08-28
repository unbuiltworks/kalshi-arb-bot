from src.weather_local_day_binding_guard import audit_weather_local_day_binding


def test_matching_explicit_local_dates_verify_read_only_binding():
    row = audit_weather_local_day_binding(
        market_date="2026-08-21",
        model_local_date="2026-08-21",
        independent_anchor_local_date="2026-08-21",
        same_local_day_verified=True,
    )
    assert row["local_day_binding_verified"] is True
    assert row["blockers"] == []
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False


def test_boolean_string_cannot_verify_local_day_equivalence():
    row = audit_weather_local_day_binding(
        market_date="2026-08-21",
        model_local_date="2026-08-21",
        independent_anchor_local_date="2026-08-21",
        same_local_day_verified="true",
    )
    assert row["local_day_binding_verified"] is False
    assert "LOCAL_DAY_EQUIVALENCE_UNVERIFIED" in row["blockers"]


def test_model_wrong_local_day_fails_closed():
    row = audit_weather_local_day_binding(
        market_date="2026-08-21",
        model_local_date="2026-08-22",
        independent_anchor_local_date="2026-08-21",
        same_local_day_verified=True,
    )
    assert row["local_day_binding_verified"] is False
    assert "FORECAST_MARKET_LOCAL_DAY_MISMATCH" in row["blockers"]


def test_anchor_wrong_local_day_fails_closed():
    row = audit_weather_local_day_binding(
        market_date="2026-08-21",
        model_local_date="2026-08-21",
        independent_anchor_local_date="2026-08-20",
        same_local_day_verified=True,
    )
    assert row["local_day_binding_verified"] is False
    assert "ANCHOR_MARKET_LOCAL_DAY_MISMATCH" in row["blockers"]


def test_missing_or_malformed_explicit_dates_fail_closed():
    row = audit_weather_local_day_binding(
        market_date="2026-08-21",
        model_local_date=None,
        independent_anchor_local_date="08/21/2026",
        same_local_day_verified=True,
    )
    assert row["local_day_binding_verified"] is False
    assert "MODEL_LOCAL_DATE_UNVERIFIED" in row["blockers"]
    assert "ANCHOR_LOCAL_DATE_UNVERIFIED" in row["blockers"]

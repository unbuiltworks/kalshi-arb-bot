from src.weather_fee_resolution_binding_guard import audit_weather_fee_resolution_binding


def _row():
    return {
        "series_ticker": "KXHIGHMIA",
        "fee_resolution_series_ticker": "KXHIGHMIA",
        "fee_type": "quadratic",
        "fee_resolution_fee_type": "quadratic",
        "fee_multiplier": "1",
        "fee_resolution_fee_multiplier": "1.0",
        "fee_metadata_source": "series_current",
        "fee_resolution_source": "series_current",
        "fee_resolution_verified": True,
        "fee_schedule_feed_complete": True,
        "quote_observed_at": "2026-08-24T04:45:00Z",
        "fee_resolution_observed_at": "2026-08-24T04:45:00.200Z",
        "fee_schedule_observed_at": "2026-08-24T04:45:00.150Z",
        "fee_resolution_effective_at": "2026-08-20T00:00:00Z",
    }


def test_accepts_exact_quote_bound_fee_resolution():
    result = audit_weather_fee_resolution_binding(_row())
    assert result["fee_resolution_binding_verified"] is True
    assert result["fee_resolution_binding_blockers"] == []
    assert result["order_entry"] is False
    assert result["live_probe_ready"] is False


def test_rejects_plausible_fee_fields_without_resolver_proof():
    row = _row()
    row.pop("fee_resolution_verified")
    result = audit_weather_fee_resolution_binding(row)
    assert result["fee_resolution_binding_verified"] is False
    assert "FEE_RESOLUTION_NOT_VERIFIED" in result["fee_resolution_binding_blockers"]


def test_rejects_stringly_resolver_verification():
    row = _row()
    row["fee_resolution_verified"] = "true"
    result = audit_weather_fee_resolution_binding(row)
    assert result["fee_resolution_binding_verified"] is False
    assert "FEE_RESOLUTION_NOT_VERIFIED" in result["fee_resolution_binding_blockers"]


def test_rejects_wrong_series_and_mismatched_fee_value():
    row = _row()
    row["fee_resolution_series_ticker"] = "KXHIGHNY"
    row["fee_resolution_fee_multiplier"] = "0.5"
    result = audit_weather_fee_resolution_binding(row)
    assert result["fee_resolution_binding_verified"] is False
    assert "FEE_RESOLUTION_SERIES_MISMATCH" in result["fee_resolution_binding_blockers"]
    assert "FEE_RESOLUTION_MULTIPLIER_MISMATCH" in result["fee_resolution_binding_blockers"]


def test_rejects_incomplete_fee_feed_and_future_effective_record():
    row = _row()
    row["fee_schedule_feed_complete"] = False
    row["fee_resolution_effective_at"] = "2026-08-24T04:46:00Z"
    result = audit_weather_fee_resolution_binding(row)
    assert result["fee_resolution_binding_verified"] is False
    assert "FEE_SCHEDULE_COMPLETENESS_UNVERIFIED" in result["fee_resolution_binding_blockers"]
    assert "FEE_RESOLUTION_FUTURE_EFFECTIVE_RECORD" in result["fee_resolution_binding_blockers"]


def test_rejects_resolution_or_schedule_observed_before_quote():
    row = _row()
    row["fee_resolution_observed_at"] = "2026-08-24T04:44:59Z"
    row["fee_schedule_observed_at"] = "2026-08-24T04:44:58Z"
    result = audit_weather_fee_resolution_binding(row)
    assert result["fee_resolution_binding_verified"] is False
    assert "FEE_RESOLUTION_OBSERVED_BEFORE_QUOTE" in result["fee_resolution_binding_blockers"]

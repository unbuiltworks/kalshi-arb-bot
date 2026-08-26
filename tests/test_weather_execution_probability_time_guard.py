from src.weather_execution_probability_time_guard import audit_weather_execution_probability_time_binding


def _row():
    return {
        "quote_observed_at": "2026-08-26T21:00:00Z",
        "executable_probability_window_start": "2026-08-26T18:00:00+00:00",
        "executable_probability_window_end": "2026-08-26T20:59:59+00:00",
        "executable_probability_time_scope_verified": True,
    }


def test_execution_probability_window_must_end_by_quote_time():
    out = audit_weather_execution_probability_time_binding(_row())
    assert out["weather_execution_probability_time_verified"] is True
    assert out["execution_probability_lookahead_detected"] is False
    assert out["blockers"] == []
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_future_samples_cannot_certify_current_quote():
    row = _row()
    row["executable_probability_window_end"] = "2026-08-26T21:00:01Z"
    out = audit_weather_execution_probability_time_binding(row)
    assert out["weather_execution_probability_time_verified"] is False
    assert out["execution_probability_lookahead_detected"] is True
    assert "WEATHER_EXECUTION_SAMPLE_LOOKAHEAD_DETECTED" in out["blockers"]


def test_time_scope_verification_must_be_literal_true():
    row = _row()
    row["executable_probability_time_scope_verified"] = "true"
    out = audit_weather_execution_probability_time_binding(row)
    assert out["weather_execution_probability_time_verified"] is False
    assert "WEATHER_EXECUTION_SAMPLE_TIME_UNVERIFIED" in out["blockers"]


def test_naive_or_missing_timestamps_fail_closed():
    cases = (
        ("quote_observed_at", "2026-08-26T21:00:00"),
        ("executable_probability_window_start", None),
        ("executable_probability_window_end", "not-a-time"),
    )
    for key, value in cases:
        row = _row()
        row[key] = value
        out = audit_weather_execution_probability_time_binding(row)
        assert out["weather_execution_probability_time_verified"] is False
        assert "WEATHER_EXECUTION_SAMPLE_TIME_UNVERIFIED" in out["blockers"]


def test_reversed_window_fails_closed():
    row = _row()
    row["executable_probability_window_start"] = "2026-08-26T20:00:00Z"
    row["executable_probability_window_end"] = "2026-08-26T19:00:00Z"
    out = audit_weather_execution_probability_time_binding(row)
    assert out["weather_execution_probability_time_verified"] is False
    assert "WEATHER_EXECUTION_SAMPLE_TIME_UNVERIFIED" in out["blockers"]

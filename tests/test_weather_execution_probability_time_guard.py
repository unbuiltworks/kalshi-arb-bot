from src.weather_execution_probability_time_guard import audit_weather_execution_probability_time_binding


def _row():
    return {
        "quote_observed_at": "2026-08-26T21:00:00Z",
        "executable_probability_window_start": "2026-08-26T18:00:00+00:00",
        "executable_probability_window_end": "2026-08-26T20:59:59+00:00",
        "executable_probability_time_scope_verified": True,
        "executable_probability_recency_verified": True,
        "executable_probability_max_age_seconds": "60",
        "executable_probability_window_span_verified": True,
        "executable_probability_max_window_seconds": "10800",
    }


def test_execution_probability_window_must_end_by_quote_time_and_be_recent():
    out = audit_weather_execution_probability_time_binding(_row())
    assert out["weather_execution_probability_time_verified"] is True
    assert out["weather_execution_probability_recency_verified"] is True
    assert out["execution_probability_lookahead_detected"] is False
    assert out["execution_probability_stale_detected"] is False
    assert out["execution_probability_age_seconds"] == "1.0"
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


def test_recency_verification_must_be_literal_true_with_positive_bound():
    cases = (
        ("executable_probability_recency_verified", "true"),
        ("executable_probability_max_age_seconds", None),
        ("executable_probability_max_age_seconds", "0"),
        ("executable_probability_max_age_seconds", "NaN"),
    )
    for key, value in cases:
        row = _row()
        row[key] = value
        out = audit_weather_execution_probability_time_binding(row)
        assert out["weather_execution_probability_time_verified"] is False
        assert out["weather_execution_probability_recency_verified"] is False
        assert "WEATHER_EXECUTION_SAMPLE_RECENCY_UNVERIFIED" in out["blockers"]


def test_old_but_non_lookahead_cohort_fails_closed_as_stale():
    row = _row()
    row["executable_probability_window_end"] = "2026-08-26T20:58:59Z"
    row["executable_probability_max_age_seconds"] = "60"
    out = audit_weather_execution_probability_time_binding(row)
    assert out["weather_execution_probability_time_verified"] is False
    assert out["execution_probability_lookahead_detected"] is False
    assert out["execution_probability_stale_detected"] is True
    assert out["execution_probability_age_seconds"] == "61.0"
    assert "WEATHER_EXECUTION_SAMPLE_STALE" in out["blockers"]


def test_recency_boundary_is_inclusive():
    row = _row()
    row["executable_probability_window_end"] = "2026-08-26T20:59:00Z"
    row["executable_probability_max_age_seconds"] = "60"
    out = audit_weather_execution_probability_time_binding(row)
    assert out["weather_execution_probability_time_verified"] is True
    assert out["execution_probability_stale_detected"] is False
    assert out["execution_probability_age_seconds"] == "60.0"


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

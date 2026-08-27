from src.weather_execution_probability_time_guard import (
    EXECUTION_SAMPLE_WINDOW_SPAN_UNVERIFIED,
    EXECUTION_SAMPLE_WINDOW_TOO_BROAD,
    audit_weather_execution_probability_time_binding,
)


def _row():
    return {
        "quote_observed_at": "2026-08-26T21:00:00-04:00",
        "executable_probability_window_start": "2026-08-26T19:00:00-04:00",
        "executable_probability_window_end": "2026-08-26T20:59:00-04:00",
        "executable_probability_time_scope_verified": True,
        "executable_probability_max_age_seconds": "300",
        "executable_probability_recency_verified": True,
        "executable_probability_max_window_seconds": "7200",
        "executable_probability_window_span_verified": True,
    }


def test_recent_bounded_window_verifies():
    result = audit_weather_execution_probability_time_binding(_row())
    assert result["weather_execution_probability_time_verified"] is True
    assert result["weather_execution_probability_window_span_verified"] is True
    assert result["execution_probability_window_too_broad_detected"] is False
    assert result["order_entry"] is False
    assert result["live_probe_ready"] is False


def test_recent_end_cannot_rescue_arbitrarily_broad_history():
    row = _row()
    row["executable_probability_window_start"] = "2026-08-20T20:59:00-04:00"
    result = audit_weather_execution_probability_time_binding(row)
    assert result["weather_execution_probability_time_verified"] is False
    assert result["execution_probability_stale_detected"] is False
    assert result["execution_probability_window_too_broad_detected"] is True
    assert EXECUTION_SAMPLE_WINDOW_TOO_BROAD in result["blockers"]


def test_window_span_requires_literal_verified_evidence():
    row = _row()
    row["executable_probability_window_span_verified"] = "true"
    result = audit_weather_execution_probability_time_binding(row)
    assert result["weather_execution_probability_time_verified"] is False
    assert result["weather_execution_probability_window_span_verified"] is False
    assert EXECUTION_SAMPLE_WINDOW_SPAN_UNVERIFIED in result["blockers"]


def test_missing_or_nonpositive_max_window_fails_closed():
    for value in (None, "", "0", "-1", "NaN", "Infinity", True):
        row = _row()
        row["executable_probability_max_window_seconds"] = value
        result = audit_weather_execution_probability_time_binding(row)
        assert result["weather_execution_probability_time_verified"] is False
        assert EXECUTION_SAMPLE_WINDOW_SPAN_UNVERIFIED in result["blockers"]

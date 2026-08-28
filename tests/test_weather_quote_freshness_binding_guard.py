from src.weather_quote_freshness_binding_guard import audit_weather_quote_freshness_binding


def _row():
    return {
        "quote_observed_at": "2026-08-24T09:00:00.000Z",
        "quote_age_evaluated_at": "2026-08-24T09:00:00.100Z",
        "quote_age_ms": 100,
        "max_quote_age_ms": 2_000,
        "quote_age_tolerance_ms": 5,
    }


def test_exact_bound_quote_age_verifies_read_only():
    out = audit_weather_quote_freshness_binding(_row())
    assert out["quote_freshness_binding_verified"] is True
    assert out["quote_freshness_binding_blockers"] == []
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_reported_age_cannot_disagree_with_timestamps():
    row = _row()
    row["quote_age_ms"] = 10
    out = audit_weather_quote_freshness_binding(row)
    assert out["quote_freshness_binding_verified"] is False
    assert "QUOTE_AGE_TIMESTAMP_MISMATCH" in out["quote_freshness_binding_blockers"]


def test_future_quote_fails_closed():
    row = _row()
    row["quote_observed_at"] = "2026-08-24T09:00:00.200Z"
    out = audit_weather_quote_freshness_binding(row)
    assert out["quote_freshness_binding_verified"] is False
    assert "QUOTE_TIMESTAMP_IN_FUTURE" in out["quote_freshness_binding_blockers"]


def test_derived_age_over_limit_is_stale_even_if_reported_age_is_small():
    row = _row()
    row["quote_age_evaluated_at"] = "2026-08-24T09:00:03.000Z"
    row["quote_age_ms"] = 10
    out = audit_weather_quote_freshness_binding(row)
    assert out["quote_freshness_binding_verified"] is False
    assert "QUOTE_STALE" in out["quote_freshness_binding_blockers"]
    assert "QUOTE_AGE_TIMESTAMP_MISMATCH" in out["quote_freshness_binding_blockers"]


def test_missing_naive_and_stringly_numeric_evidence_fail_closed():
    cases = [
        {"quote_age_evaluated_at": None},
        {"quote_age_evaluated_at": "2026-08-24T09:00:00.100"},
        {"quote_age_ms": True},
        {"max_quote_age_ms": "NaN"},
        {"quote_age_tolerance_ms": "Infinity"},
    ]
    for patch in cases:
        row = _row()
        row.update(patch)
        out = audit_weather_quote_freshness_binding(row)
        assert out["quote_freshness_binding_verified"] is False

from src.weather_model_run_quote_guard import audit_model_run_quote_time


def test_model_run_before_quote_passes_read_only_shadow_guard():
    out = audit_model_run_quote_time(
        model_run_time="2026-08-23T12:00:00Z",
        quote_observed_at="2026-08-23T12:05:00Z",
    )
    assert out["model_run_quote_time_verified"] is True
    assert out["classification"] == "MODEL_RUN_QUOTE_TIME_VERIFIED_SHADOW"
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_model_run_after_quote_is_rejected_as_lookahead():
    out = audit_model_run_quote_time(
        model_run_time="2026-08-23T12:05:00Z",
        quote_observed_at="2026-08-23T12:00:00Z",
    )
    assert out["model_run_quote_time_verified"] is False
    assert "MODEL_RUN_LOOKAHEAD_DETECTED" in out["blockers"]


def test_missing_or_naive_time_provenance_fails_closed():
    missing = audit_model_run_quote_time(
        model_run_time=None,
        quote_observed_at="2026-08-23T12:00:00Z",
    )
    naive = audit_model_run_quote_time(
        model_run_time="2026-08-23T11:00:00",
        quote_observed_at="2026-08-23T12:00:00Z",
    )
    assert missing["model_run_quote_time_verified"] is False
    assert naive["model_run_quote_time_verified"] is False
    assert "MODEL_RUN_QUOTE_TIME_PROVENANCE_INVALID" in missing["blockers"]
    assert "MODEL_RUN_QUOTE_TIME_PROVENANCE_INVALID" in naive["blockers"]

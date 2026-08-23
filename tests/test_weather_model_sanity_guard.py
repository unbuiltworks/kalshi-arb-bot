from src.weather_model_sanity_guard import audit_weather_model_sanity


def base(**overrides):
    row = dict(
        model_mean_f="88.8",
        model_station_id="KMIA",
        model_id="GFS:v1",
        independent_anchor_high_f="92.0",
        independent_anchor_station_id="KMIA",
        independent_anchor_verified=True,
        independent_anchor_source_id="NWS:KMIA",
        independent_anchor_observed_at="2026-08-21T05:30:00Z",
        same_local_day_verified=True,
        disagreement_tolerance_f="2.0",
        disagreement_tolerance_audited=True,
        model_calibration_verified=True,
        calibration_model_id="GFS:v1",
        calibration_target="DAILY_MAX_TEMPERATURE_F",
        calibration_evidence_verified=True,
        calibration_evidence_id="weather-calibration:KMIA:2026-08-01",
        calibration_sample_count=60,
        calibration_station_id="KMIA",
        calibration_window_end="2026-08-01T23:59:59Z",
        model_run_time="2026-08-21T06:00:00Z",
        quote_observed_at="2026-08-21T07:30:00Z",
    )
    row.update(overrides)
    return audit_weather_model_sanity(**row)


def test_large_model_anchor_disagreement_stays_research_only():
    out = base()
    assert out["model_sanity_verified"] is False
    assert out["model_anchor_discrepancy_f"] == "3.2"
    assert "MODEL_ANCHOR_DISAGREEMENT_EXCEEDS_AUDITED_TOLERANCE" in out["blockers"]
    assert out["live_probe_ready"] is False


def test_close_same_station_audited_model_can_pass_shadow_sanity_only():
    out = base(model_mean_f="91.2")
    assert out["model_sanity_verified"] is True
    assert out["classification"] == "MODEL_SANITY_VERIFIED_SHADOW"
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_stringly_flags_fail_closed():
    out = base(independent_anchor_verified="true", model_calibration_verified="true")
    assert out["model_sanity_verified"] is False
    assert "INDEPENDENT_FORECAST_ANCHOR_UNVERIFIED" in out["blockers"]
    assert "MODEL_CALIBRATION_UNVERIFIED" in out["blockers"]


def test_station_mismatch_fails_closed():
    out = base(independent_anchor_station_id="KFLL")
    assert out["model_sanity_verified"] is False
    assert "FORECAST_STATION_MISMATCH" in out["blockers"]


def test_unaudited_tolerance_cannot_certify():
    out = base(model_mean_f="91.8", disagreement_tolerance_audited=False)
    assert out["model_sanity_verified"] is False
    assert "MODEL_DISAGREEMENT_TOLERANCE_UNAUDITED" in out["blockers"]


def test_bare_calibration_booleans_without_provenance_fail_closed():
    out = base(
        model_mean_f="91.8",
        calibration_evidence_id=None,
        calibration_sample_count=None,
        calibration_station_id=None,
        calibration_window_end=None,
    )
    assert out["model_sanity_verified"] is False
    assert "MODEL_CALIBRATION_EVIDENCE_ID_MISSING" in out["blockers"]
    assert "MODEL_CALIBRATION_SAMPLE_COUNT_INVALID" in out["blockers"]
    assert "MODEL_CALIBRATION_STATION_MISMATCH" in out["blockers"]
    assert "MODEL_CALIBRATION_TIME_PROVENANCE_INVALID" in out["blockers"]


def test_future_calibration_evidence_is_rejected_as_lookahead():
    out = base(
        model_mean_f="91.8",
        calibration_window_end="2026-08-22T00:00:00Z",
        model_run_time="2026-08-21T06:00:00Z",
    )
    assert out["model_sanity_verified"] is False
    assert "MODEL_CALIBRATION_LOOKAHEAD_DETECTED" in out["blockers"]


def test_calibration_station_must_match_model_station():
    out = base(model_mean_f="91.8", calibration_station_id="KFLL")
    assert out["model_sanity_verified"] is False
    assert "MODEL_CALIBRATION_STATION_MISMATCH" in out["blockers"]


def test_stringly_calibration_verification_fails_closed():
    out = base(model_mean_f="91.8", calibration_evidence_verified="true")
    assert out["model_sanity_verified"] is False
    assert "MODEL_CALIBRATION_EVIDENCE_UNVERIFIED" in out["blockers"]


def test_calibration_must_match_exact_model_identity():
    out = base(model_mean_f="91.8", calibration_model_id="ECMWF:v1")
    assert out["model_sanity_verified"] is False
    assert "MODEL_CALIBRATION_MODEL_ID_MISMATCH" in out["blockers"]


def test_calibration_target_must_be_daily_max_temperature():
    out = base(model_mean_f="91.8", calibration_target="DAILY_MEAN_TEMPERATURE_F")
    assert out["model_sanity_verified"] is False
    assert "MODEL_CALIBRATION_TARGET_MISMATCH" in out["blockers"]


def test_anchor_observed_after_quote_is_lookahead():
    out = base(model_mean_f="91.8", independent_anchor_observed_at="2026-08-21T08:00:00Z")
    assert out["model_sanity_verified"] is False
    assert "INDEPENDENT_FORECAST_ANCHOR_LOOKAHEAD_DETECTED" in out["blockers"]


def test_anchor_requires_source_provenance():
    out = base(model_mean_f="91.8", independent_anchor_source_id=None)
    assert out["model_sanity_verified"] is False
    assert "INDEPENDENT_FORECAST_ANCHOR_SOURCE_MISSING" in out["blockers"]


def test_nonfinite_values_fail_closed():
    out = base(model_mean_f="NaN")
    assert out["model_sanity_verified"] is False
    assert "MODEL_OR_ANCHOR_VALUE_INVALID" in out["blockers"]

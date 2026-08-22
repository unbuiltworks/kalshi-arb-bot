from src.weather_model_sanity_guard import audit_weather_model_sanity


def base(**overrides):
    row = dict(
        model_mean_f="88.8",
        model_station_id="KMIA",
        independent_anchor_high_f="92.0",
        independent_anchor_station_id="KMIA",
        independent_anchor_verified=True,
        same_local_day_verified=True,
        disagreement_tolerance_f="2.0",
        disagreement_tolerance_audited=True,
        model_calibration_verified=True,
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


def test_nonfinite_values_fail_closed():
    out = base(model_mean_f="NaN")
    assert out["model_sanity_verified"] is False
    assert "MODEL_OR_ANCHOR_VALUE_INVALID" in out["blockers"]

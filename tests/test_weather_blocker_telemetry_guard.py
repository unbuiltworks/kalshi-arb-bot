from src.weather_blocker_telemetry_guard import audit_weather_blocker_telemetry


def test_absent_blocker_fields_are_a_valid_empty_set():
    out = audit_weather_blocker_telemetry({})
    assert out["blocker_telemetry_verified"] is True
    assert out["blocker_source"] == "absent"
    assert out["blockers"] == []
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_readiness_blockers_are_used_only_when_blockers_key_is_absent():
    out = audit_weather_blocker_telemetry({"readiness_blockers": ["MODEL_RESEARCH_ONLY"]})
    assert out["blocker_telemetry_verified"] is True
    assert out["blocker_source"] == "readiness_blockers"
    assert out["blockers"] == ["MODEL_RESEARCH_ONLY"]


def test_explicit_empty_blockers_do_not_fall_through_to_readiness_blockers():
    out = audit_weather_blocker_telemetry({
        "blockers": [],
        "readiness_blockers": ["MODEL_RESEARCH_ONLY"],
    })
    assert out["blocker_telemetry_verified"] is True
    assert out["blocker_source"] == "blockers"
    assert out["blockers"] == []


def test_falsey_malformed_blockers_fail_closed_instead_of_becoming_empty():
    for malformed in ("", 0, False, None, {}):
        out = audit_weather_blocker_telemetry({"blockers": malformed})
        assert out["blocker_telemetry_verified"] is False
        assert out["blockers"] == ["MALFORMED_BLOCKER_TELEMETRY"]
        assert out["order_entry"] is False
        assert out["live_probe_ready"] is False


def test_falsey_malformed_readiness_blockers_fail_closed_when_primary_absent():
    for malformed in ("", 0, False, None, {}):
        out = audit_weather_blocker_telemetry({"readiness_blockers": malformed})
        assert out["blocker_telemetry_verified"] is False
        assert out["blockers"] == ["MALFORMED_BLOCKER_TELEMETRY"]


def test_malformed_entries_inside_valid_blocker_container_fail_closed():
    for malformed_values in ([None], [""], ["   "], [0], [False], [{}], ["MODEL_RESEARCH_ONLY", None]):
        out = audit_weather_blocker_telemetry({"blockers": malformed_values})
        assert out["blocker_telemetry_verified"] is False
        assert out["blocker_source"] == "blockers"
        assert out["blockers"] == ["MALFORMED_BLOCKER_TELEMETRY"]
        assert out["order_entry"] is False
        assert out["live_probe_ready"] is False


def test_duplicate_valid_string_entries_are_normalized_without_weakening_blockers():
    out = audit_weather_blocker_telemetry({
        "blockers": [" MODEL_RESEARCH_ONLY ", "MODEL_RESEARCH_ONLY", "TWC_SOURCE_ACCESS_REQUIRED"],
    })
    assert out["blocker_telemetry_verified"] is True
    assert out["blockers"] == ["MODEL_RESEARCH_ONLY", "TWC_SOURCE_ACCESS_REQUIRED"]

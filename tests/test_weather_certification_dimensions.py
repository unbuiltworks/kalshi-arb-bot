from src.weather_certification_dimensions import audit_weather_certification_dimensions


def _clean_row():
    return {
        "blockers": [],
        "exact_market_source_verified": True,
        "ev_certified_shadow": True,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }


def test_current_runtime_weather_blockers_stay_research_only():
    row = _clean_row()
    row["readiness_blockers"] = [
        "TWC_SOURCE_ACCESS_REQUIRED",
        "MODEL_RESEARCH_ONLY",
        "EXACT_MARKET_SOURCE_AUDIT_REQUIRED",
        "AUTHORITATIVE_FEE_AUDIT_REQUIRED",
        "MODEL_SANITY_AUDIT_REQUIRED",
    ]
    row.pop("blockers")
    row["exact_market_source_verified"] = False
    row["ev_certified_shadow"] = False

    out = audit_weather_certification_dimensions(row)

    assert out["settlement_rules_provenance_verified"] is False
    assert out["fee_economics_verified"] is False
    assert out["model_probability_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False
    assert out["live_probe_ready"] is False
    assert out["order_entry"] is False


def test_fee_blocker_does_not_erase_settlement_provenance_dimension():
    row = _clean_row()
    row["blockers"] = ["AUTHORITATIVE_FEE_AUDIT_REQUIRED"]
    row["ev_certified_shadow"] = False

    out = audit_weather_certification_dimensions(row)

    assert out["settlement_rules_provenance_verified"] is True
    assert out["fee_economics_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False


def test_exact_market_source_must_be_literal_true():
    for value in (None, False, "true", 1):
        row = _clean_row()
        row["exact_market_source_verified"] = value
        row["ev_certified_shadow"] = False
        out = audit_weather_certification_dimensions(row)
        assert out["settlement_rules_provenance_verified"] is False
        assert "EXACT_MARKET_SOURCE_AUDIT_REQUIRED" in out["settlement_blockers"]


def test_only_fully_certified_read_only_shadow_can_clear_prereqs():
    out = audit_weather_certification_dimensions(_clean_row())

    assert out["settlement_rules_provenance_verified"] is True
    assert out["market_data_verified"] is True
    assert out["fee_economics_verified"] is True
    assert out["model_probability_verified"] is True
    assert out["executable_shadow_prereqs_verified"] is True
    assert out["live_probe_ready"] is False


def test_unknown_blocker_fails_closed():
    row = _clean_row()
    row["blockers"] = ["SOMETHING_NEW_AND_UNCLASSIFIED"]

    out = audit_weather_certification_dimensions(row)

    assert out["unclassified_blockers"] == ["SOMETHING_NEW_AND_UNCLASSIFIED"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_string_boolean_cannot_clear_upstream_ev_gate():
    row = _clean_row()
    row["ev_certified_shadow"] = "true"

    out = audit_weather_certification_dimensions(row)

    assert out["executable_shadow_prereqs_verified"] is False

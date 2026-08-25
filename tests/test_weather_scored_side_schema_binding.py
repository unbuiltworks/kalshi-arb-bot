from src.weather_scored_side_book_binding_guard import audit_weather_scored_side_book_binding


def _row():
    return {
        "best_shadow_side": "NO",
        "modeled_after_fee_ev_cents": "12.5",
        "no_ask_cents": 40,
        "no_available_contracts": 5,
        "side_economics": [
            {
                "side": "NO",
                "ask_cents": 40,
                "available_contracts": 5,
                "modeled_after_fee_ev_cents": "12.5",
            }
        ],
    }


def test_scored_side_string_schema_binds_to_matching_book_and_ev():
    result = audit_weather_scored_side_book_binding(_row())
    assert result["scored_side_book_binding_verified"] is True
    assert result["scored_side"] == "NO"
    assert result["scored_side_modeled_after_fee_ev_cents"] == "12.5"
    assert result["blockers"] == []


def test_legacy_best_side_object_is_detected_and_fails_closed():
    row = _row()
    row["best_shadow_side"] = dict(row["side_economics"][0])
    result = audit_weather_scored_side_book_binding(row)
    assert result["scored_side"] == "NO"
    assert result["legacy_scored_side_schema_detected"] is True
    assert result["scored_side_book_binding_verified"] is False
    assert "LEGACY_SCORED_SIDE_OBJECT_SCHEMA" in result["blockers"]


def test_wrong_top_level_ev_cannot_ride_on_valid_selected_side():
    row = _row()
    row["modeled_after_fee_ev_cents"] = "2.0"
    result = audit_weather_scored_side_book_binding(row)
    assert result["scored_side_book_binding_verified"] is False
    assert "SCORED_SIDE_EV_MISMATCH" in result["blockers"]


def test_stale_selected_ask_cannot_ride_on_current_executable_book():
    row = _row()
    row["side_economics"][0]["ask_cents"] = 35
    result = audit_weather_scored_side_book_binding(row)
    assert result["scored_side_book_binding_verified"] is False
    assert "SCORED_SIDE_ASK_MISMATCH" in result["blockers"]


def test_selected_quantity_cannot_exceed_displayed_depth():
    row = _row()
    row["side_economics"][0]["available_contracts"] = 6
    result = audit_weather_scored_side_book_binding(row)
    assert result["scored_side_book_binding_verified"] is False
    assert "SCORED_SIDE_QUANTITY_EXCEEDS_DEPTH" in result["blockers"]

from src.weather_scored_side_book_binding_guard import audit_weather_scored_side_book_binding


def _row():
    return {
        "best_shadow_side": "NO",
        "modeled_after_fee_ev_cents": "12.5",
        "yes_ask_cents": 60,
        "yes_available_contracts": 5,
        "no_ask_cents": 41,
        "no_available_contracts": 3,
        "side_economics": [
            {"side": "YES", "ask_cents": 60, "available_contracts": 5, "modeled_after_fee_ev_cents": "-15.0"},
            {"side": "NO", "ask_cents": 41, "available_contracts": 3, "modeled_after_fee_ev_cents": "12.5"},
        ],
    }


def test_scored_side_with_q1_depth_and_matching_ev_verifies():
    out = audit_weather_scored_side_book_binding(_row())
    assert out["scored_side"] == "NO"
    assert out["scored_side_book_binding_verified"] is True
    assert out["blockers"] == []
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_other_side_depth_cannot_rescue_unexecutable_scored_side():
    row = _row()
    row["no_available_contracts"] = 0
    out = audit_weather_scored_side_book_binding(row)
    assert out["scored_side_book_binding_verified"] is False
    assert "SCORED_SIDE_EXECUTABLE_BOOK_UNVERIFIED" in out["blockers"]
    assert row["yes_available_contracts"] >= 1


def test_fixed_point_sub_q1_depth_cannot_be_rescued_by_legacy_q1_on_scored_side():
    row = _row()
    row.update({"no_ask_dollars": "0.4100", "no_ask_qty_fp": "0.99"})
    out = audit_weather_scored_side_book_binding(row)
    assert out["scored_side_book_binding_verified"] is False
    assert "SCORED_SIDE_EXECUTABLE_BOOK_UNVERIFIED" in out["blockers"]


def test_missing_or_invalid_scored_side_fails_closed():
    for value in (None, "", "MAYBE", True):
        row = _row()
        row["best_shadow_side"] = value
        out = audit_weather_scored_side_book_binding(row)
        assert out["scored_side_book_binding_verified"] is False
        assert "SCORED_SIDE_REQUIRED" in out["blockers"]


def test_summary_ev_must_match_selected_side_economics():
    row = _row()
    row["modeled_after_fee_ev_cents"] = "13.0"
    out = audit_weather_scored_side_book_binding(row)
    assert out["scored_side_book_binding_verified"] is False
    assert "SCORED_SIDE_EV_MISMATCH" in out["blockers"]


def test_side_economics_identity_must_be_unique():
    row = _row()
    row["side_economics"].append({"side": "NO", "ask_cents": 41, "available_contracts": 3, "modeled_after_fee_ev_cents": "12.5"})
    out = audit_weather_scored_side_book_binding(row)
    assert out["scored_side_book_binding_verified"] is False
    assert "SCORED_SIDE_ECONOMICS_IDENTITY_UNVERIFIED" in out["blockers"]


def test_nonfinite_or_missing_ev_fails_closed():
    for value in (None, "NaN", "Infinity"):
        row = _row()
        row["modeled_after_fee_ev_cents"] = value
        out = audit_weather_scored_side_book_binding(row)
        assert out["scored_side_book_binding_verified"] is False
        assert "SCORED_SIDE_EV_REQUIRED" in out["blockers"]


def test_selected_economics_ask_must_match_audited_book():
    row = _row()
    row["side_economics"][1]["ask_cents"] = 35
    out = audit_weather_scored_side_book_binding(row)
    assert out["scored_side_book_binding_verified"] is False
    assert "SCORED_SIDE_ASK_MISMATCH" in out["blockers"]


def test_selected_economics_quantity_cannot_exceed_displayed_depth():
    row = _row()
    row["side_economics"][1]["available_contracts"] = 4
    out = audit_weather_scored_side_book_binding(row)
    assert out["scored_side_book_binding_verified"] is False
    assert "SCORED_SIDE_QUANTITY_EXCEEDS_DEPTH" in out["blockers"]


def test_fixed_point_selected_economics_must_match_fixed_point_book():
    row = _row()
    row.update({"no_ask_dollars": "0.4100", "no_ask_qty_fp": "2.50"})
    row["no_available_contracts"] = 2
    row["side_economics"][1] = {
        "side": "NO", "ask_dollars": "0.4100", "quantity": "2.50", "modeled_after_fee_ev_cents": "12.5"
    }
    out = audit_weather_scored_side_book_binding(row)
    assert out["scored_side_book_binding_verified"] is True

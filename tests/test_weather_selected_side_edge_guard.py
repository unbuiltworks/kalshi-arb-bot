from src.weather_selected_side_edge_guard import audit_weather_selected_side_edge


def _row():
    return {
        "best_shadow_side": "NO",
        "modeled_after_fee_edge_cents_per_contract": "17.5",
        "side_economics": [
            {
                "side": "YES",
                "available_contracts": 1,
                "modeled_after_fee_ev_cents": "-22",
                "modeled_after_fee_edge_cents_per_contract": "-22",
            },
            {
                "side": "NO",
                "available_contracts": 1,
                "modeled_after_fee_ev_cents": "17.5",
                "modeled_after_fee_edge_cents_per_contract": "17.5",
            },
        ],
    }


def test_edge_per_contract_verifies_for_both_sides():
    out = audit_weather_selected_side_edge(_row())
    assert out["selected_side_edge_per_contract_verified"] is True
    assert out["recomputed_side_edge_cents_per_contract"] == {"NO": "17.5", "YES": "-22"}
    assert out["blockers"] == []
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_selected_side_fabricated_edge_fails_closed():
    row = _row()
    row["side_economics"][1]["modeled_after_fee_edge_cents_per_contract"] = "40"
    row["modeled_after_fee_edge_cents_per_contract"] = "40"
    out = audit_weather_selected_side_edge(row)
    assert out["selected_side_edge_per_contract_verified"] is False
    assert "SELECTED_SIDE_EDGE_ARITHMETIC_MISMATCH" in out["blockers"]


def test_opposite_side_fabricated_edge_fails_closed():
    row = _row()
    row["side_economics"][0]["modeled_after_fee_edge_cents_per_contract"] = "-99"
    out = audit_weather_selected_side_edge(row)
    assert out["selected_side_edge_per_contract_verified"] is False
    assert "OPPOSITE_SIDE_EDGE_ARITHMETIC_MISMATCH" in out["blockers"]


def test_fractional_quantity_edge_is_total_ev_divided_by_quantity():
    row = _row()
    row["side_economics"][1].update(
        available_contracts=None,
        quantity="2.5",
        modeled_after_fee_ev_cents="45",
        modeled_after_fee_edge_cents_per_contract="18",
    )
    row["modeled_after_fee_edge_cents_per_contract"] = "18"
    out = audit_weather_selected_side_edge(row)
    assert out["selected_side_edge_per_contract_verified"] is True
    assert out["recomputed_side_edge_cents_per_contract"]["NO"] == "18"


def test_top_level_edge_must_match_selected_record():
    row = _row()
    row["modeled_after_fee_edge_cents_per_contract"] = "99"
    out = audit_weather_selected_side_edge(row)
    assert out["selected_side_edge_per_contract_verified"] is False
    assert "SELECTED_SIDE_TOP_LEVEL_EDGE_MISMATCH" in out["blockers"]


def test_missing_or_nonfinite_edge_fails_closed():
    for value in (None, "NaN", "Infinity"):
        row = _row()
        row["side_economics"][1]["modeled_after_fee_edge_cents_per_contract"] = value
        out = audit_weather_selected_side_edge(row)
        assert out["selected_side_edge_per_contract_verified"] is False

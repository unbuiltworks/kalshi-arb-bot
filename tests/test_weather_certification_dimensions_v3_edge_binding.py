from src.weather_certification_dimensions_v3 import audit_weather_certification_dimensions_v3


def _row():
    return {
        "blockers": [],
        "best_shadow_side": "NO",
        "side_economics": [
            {
                "side": "YES",
                "ask_cents": 60,
                "available_contracts": 1,
                "forecast_side_probability": "0.40",
                "modeled_taker_fee_cents": "1",
                "modeled_after_fee_ev_cents": "-21",
                "modeled_after_fee_edge_cents_per_contract": "-21",
            },
            {
                "side": "NO",
                "ask_cents": 40,
                "available_contracts": 1,
                "forecast_side_probability": "0.60",
                "modeled_taker_fee_cents": "1",
                "modeled_after_fee_ev_cents": "19",
                "modeled_after_fee_edge_cents_per_contract": "19",
            },
        ],
        "modeled_taker_fee_cents": "1",
        "modeled_after_fee_ev_cents": "19",
        "modeled_after_fee_edge_cents_per_contract": "19",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }


def _fake_base(_row):
    return {
        "market_data_verified": True,
        "market_data_blockers": [],
        "executable_shadow_prereqs_verified": True,
    }


def test_canonical_v3_requires_edge_per_contract_arithmetic(monkeypatch):
    monkeypatch.setattr(
        "src.weather_certification_dimensions_v3.audit_weather_certification_dimensions",
        _fake_base,
    )
    row = _row()
    row["side_economics"][1]["modeled_after_fee_edge_cents_per_contract"] = "99"
    row["modeled_after_fee_edge_cents_per_contract"] = "99"

    out = audit_weather_certification_dimensions_v3(row)

    assert out["selected_side_schema_verified"] is True
    assert out["selected_side_economics_arithmetic_verified"] is True
    assert out["selected_side_edge_per_contract_verified"] is False
    assert "SELECTED_SIDE_EDGE_ARITHMETIC_MISMATCH" in out["market_data_blockers"]
    assert out["market_data_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_canonical_v3_accepts_coherent_edge_per_contract(monkeypatch):
    monkeypatch.setattr(
        "src.weather_certification_dimensions_v3.audit_weather_certification_dimensions",
        _fake_base,
    )
    out = audit_weather_certification_dimensions_v3(_row())
    assert out["selected_side_edge_per_contract_verified"] is True
    assert out["recomputed_side_edge_cents_per_contract"] == {"NO": "19", "YES": "-21"}
    assert out["executable_shadow_prereqs_verified"] is True

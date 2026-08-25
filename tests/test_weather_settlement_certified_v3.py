import src.weather_settlement_certified_v3 as v3


def _call(monkeypatch, row):
    monkeypatch.setattr(v3, "build_v2_shadow_row", lambda **_: row)
    return v3.build_exact_market_certified_shadow_row(
        series={}, market={}, quote={}, station={}, forecast=None
    )


def test_no_side_selection_rebinds_top_level_economics(monkeypatch):
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "ev_certified_shadow": True,
        # Legacy v2 shape: full selected object, but stale YES top-level economics.
        "best_shadow_side": {
            "side": "NO",
            "modeled_taker_fee_cents": "2",
            "modeled_after_fee_ev_cents": "18.5",
            "modeled_after_fee_edge_cents_per_contract": "3.7",
        },
        "modeled_taker_fee_cents": "1",
        "modeled_after_fee_ev_cents": "4.0",
        "modeled_after_fee_edge_cents_per_contract": "0.8",
        "side_economics": [
            {
                "side": "YES",
                "modeled_taker_fee_cents": "1",
                "modeled_after_fee_ev_cents": "4.0",
                "modeled_after_fee_edge_cents_per_contract": "0.8",
            },
            {
                "side": "NO",
                "modeled_taker_fee_cents": "2",
                "modeled_after_fee_ev_cents": "18.5",
                "modeled_after_fee_edge_cents_per_contract": "3.7",
            },
        ],
        "blockers": [],
    }

    out = _call(monkeypatch, row)

    assert out["selected_side_schema_verified"] is True
    assert out["best_shadow_side"] == "NO"
    assert out["modeled_taker_fee_cents"] == "2"
    assert out["modeled_after_fee_ev_cents"] == "18.5"
    assert out["modeled_after_fee_edge_cents_per_contract"] == "3.7"
    assert out["best_shadow_side_detail"]["side"] == "NO"
    assert out["ev_certified_shadow"] is True
    assert out["read_only"] is True
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_ambiguous_selected_side_fails_closed(monkeypatch):
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "ev_certified_shadow": True,
        "best_shadow_side": "NO",
        "modeled_taker_fee_cents": "1",
        "modeled_after_fee_ev_cents": "10",
        "side_economics": [
            {"side": "NO", "modeled_taker_fee_cents": "1", "modeled_after_fee_ev_cents": "10"},
            {"side": "NO", "modeled_taker_fee_cents": "1", "modeled_after_fee_ev_cents": "9"},
        ],
        "blockers": [],
    }

    out = _call(monkeypatch, row)

    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["modeled_taker_fee_cents"] is None
    assert out["modeled_after_fee_ev_cents"] is None
    assert "SELECTED_SIDE_ECONOMICS_AMBIGUOUS" in out["blockers"]
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False

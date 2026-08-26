from src.weather_certification_dimensions_v3 import audit_weather_certification_dimensions_v3


def _row(*, declared_side="NO", yes_ev="2.0", no_ev="10.0"):
    return {
        "blockers": [],
        "best_shadow_side": declared_side,
        "side_economics": [
            {
                "side": "YES",
                "modeled_taker_fee_cents": "1.0",
                "modeled_after_fee_ev_cents": yes_ev,
                "modeled_after_fee_edge_cents_per_contract": yes_ev,
            },
            {
                "side": "NO",
                "modeled_taker_fee_cents": "1.0",
                "modeled_after_fee_ev_cents": no_ev,
                "modeled_after_fee_edge_cents_per_contract": no_ev,
            },
        ],
        "modeled_taker_fee_cents": "1.0",
        "modeled_after_fee_ev_cents": no_ev if declared_side == "NO" else yes_ev,
        "modeled_after_fee_edge_cents_per_contract": no_ev if declared_side == "NO" else yes_ev,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }


def test_wrong_declared_side_fails_closed_before_portfolio_certification(monkeypatch):
    seen = {}

    def fake_base(row):
        seen.update(row)
        return {
            "market_data_verified": True,
            "market_data_blockers": [],
            "executable_shadow_prereqs_verified": True,
        }

    monkeypatch.setattr(
        "src.weather_certification_dimensions_v3.audit_weather_certification_dimensions",
        fake_base,
    )
    out = audit_weather_certification_dimensions_v3(
        _row(declared_side="YES", yes_ev="2.0", no_ev="10.0")
    )
    assert out["selected_side_schema_verified"] is False
    assert out["market_data_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False
    assert "BEST_SHADOW_SIDE_RECOMPUTE_MISMATCH" in out["market_data_blockers"]
    assert seen["modeled_after_fee_ev_cents"] is None
    assert out["order_entry"] is False and out["live_probe_ready"] is False


def test_unique_best_side_is_recomputed_before_base_audit(monkeypatch):
    seen = {}

    def fake_base(row):
        seen.update(row)
        return {
            "market_data_verified": True,
            "market_data_blockers": [],
            "executable_shadow_prereqs_verified": True,
        }

    monkeypatch.setattr(
        "src.weather_certification_dimensions_v3.audit_weather_certification_dimensions",
        fake_base,
    )
    out = audit_weather_certification_dimensions_v3(_row())
    assert out["selected_side_schema_verified"] is True
    assert out["selected_side_recomputed"] is True
    assert seen["best_shadow_side"] == "NO"
    assert seen["modeled_after_fee_ev_cents"] == "10.0"
    assert out["executable_shadow_prereqs_verified"] is True
    assert out["order_entry"] is False and out["live_probe_ready"] is False


def test_tied_side_ev_fails_closed(monkeypatch):
    def fake_base(row):
        return {
            "market_data_verified": True,
            "market_data_blockers": [],
            "executable_shadow_prereqs_verified": True,
        }

    monkeypatch.setattr(
        "src.weather_certification_dimensions_v3.audit_weather_certification_dimensions",
        fake_base,
    )
    out = audit_weather_certification_dimensions_v3(_row(no_ev="2.0"))
    assert out["selected_side_schema_verified"] is False
    assert "BEST_SHADOW_SIDE_TIE" in out["market_data_blockers"]
    assert out["executable_shadow_prereqs_verified"] is False

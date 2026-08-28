from decimal import Decimal

from src.weather_certification_dimensions_v3 import audit_weather_certification_dimensions_v3


def _side_record(side, ev):
    ev_decimal = Decimal(str(ev))
    if side == "YES":
        ask_cents = 60
        fee_cents = Decimal("1.0")
    else:
        ask_cents = 40
        fee_cents = Decimal("1.0")
    probability = (ev_decimal + Decimal(ask_cents) + fee_cents) / Decimal(100)
    return {
        "side": side,
        "ask_cents": ask_cents,
        "available_contracts": 1,
        "forecast_side_probability": format(probability, "f"),
        "modeled_taker_fee_cents": format(fee_cents, "f"),
        "modeled_after_fee_ev_cents": str(ev),
        "modeled_after_fee_edge_cents_per_contract": str(ev),
    }


def _row(*, declared_side="NO", yes_ev="-6.0", no_ev="4.0"):
    return {
        "blockers": [],
        "best_shadow_side": declared_side,
        "side_economics": [
            _side_record("YES", yes_ev),
            _side_record("NO", no_ev),
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
        _row(declared_side="YES", yes_ev="-6.0", no_ev="4.0")
    )
    assert out["selected_side_schema_verified"] is False
    assert out["selected_side_economics_arithmetic_verified"] is False
    assert out["market_data_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False
    assert "BEST_SHADOW_SIDE_RECOMPUTE_MISMATCH" in out["market_data_blockers"]
    assert seen["modeled_after_fee_ev_cents"] is None
    assert out["order_entry"] is False and out["live_probe_ready"] is False


def test_unique_best_side_and_ev_arithmetic_are_recomputed_before_base_audit(monkeypatch):
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
    assert out["selected_side_economics_arithmetic_verified"] is True
    assert out["selected_side_recomputed_after_fee_ev_cents"] == "4.00"
    assert seen["best_shadow_side"] == "NO"
    assert seen["modeled_after_fee_ev_cents"] == "4.0"
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
    out = audit_weather_certification_dimensions_v3(_row(yes_ev="-1.0", no_ev="-1.0"))
    assert out["selected_side_schema_verified"] is False
    assert out["selected_side_economics_arithmetic_verified"] is False
    assert "BEST_SHADOW_SIDE_TIE" in out["market_data_blockers"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_fabricated_selected_side_ev_fails_closed_even_when_schema_and_base_pass(monkeypatch):
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
    row = _row()
    row["side_economics"][1]["forecast_side_probability"] = "0.90"
    out = audit_weather_certification_dimensions_v3(row)
    assert out["selected_side_schema_verified"] is True
    assert out["selected_side_economics_arithmetic_verified"] is False
    assert "SELECTED_SIDE_EV_ARITHMETIC_MISMATCH" in out["market_data_blockers"]
    assert out["market_data_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False
    assert out["order_entry"] is False and out["live_probe_ready"] is False

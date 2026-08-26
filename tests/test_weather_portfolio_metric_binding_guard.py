from decimal import Decimal

from src.weather_portfolio_metric_binding_guard import audit_weather_portfolio_metric_binding


def _row():
    # NO: 41c q1 + 1.5c fee = 42.5c deployed capital; 17.5c EV.
    # Derived edge/dollar = 0.175 / 0.425 = 7/17.
    return {
        "best_shadow_side": "NO",
        "side_economics": [
            {
                "side": "YES",
                "ask_cents": 60,
                "available_contracts": 1,
                "modeled_taker_fee_cents": 2,
                "modeled_after_fee_ev_cents": -22,
            },
            {
                "side": "NO",
                "ask_cents": 41,
                "available_contracts": 1,
                "modeled_taker_fee_cents": "1.5",
                "modeled_after_fee_ev_cents": "17.5",
            },
        ],
        "net_edge_per_dollar": str(Decimal("7") / Decimal("17")),
        "depth_dollars": "0.41",
        "executable_probability": "0.25",
        "executable_probability_verified": True,
        "executable_probability_window_verified": True,
        "executable_probability_sample_count": 40,
    }


def test_portfolio_metrics_bind_to_selected_side():
    out = audit_weather_portfolio_metric_binding(_row())
    assert out["weather_portfolio_metric_binding_verified"] is True
    assert out["weather_execution_probability_verified"] is True
    assert Decimal(out["derived_depth_dollars"]) == Decimal("0.41")
    assert Decimal(out["derived_net_edge_per_dollar"]) == Decimal("7") / Decimal("17")
    assert out["blockers"] == []
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_inflated_edge_cannot_pass():
    row = _row()
    row["net_edge_per_dollar"] = "9"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_PORTFOLIO_METRICS_UNVERIFIED" in out["blockers"]


def test_inflated_depth_cannot_pass():
    row = _row()
    row["depth_dollars"] = "500"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_PORTFOLIO_METRICS_UNVERIFIED" in out["blockers"]


def test_execution_probability_needs_empirical_verification():
    for key, value in (
        ("executable_probability_verified", "true"),
        ("executable_probability_window_verified", False),
        ("executable_probability_sample_count", 0),
        ("executable_probability", "1.01"),
    ):
        row = _row()
        row[key] = value
        out = audit_weather_portfolio_metric_binding(row)
        assert out["weather_portfolio_metric_binding_verified"] is False
        assert "WEATHER_EXECUTION_PROBABILITY_UNVERIFIED" in out["blockers"]


def test_fixed_point_fractional_selected_side_is_supported():
    row = _row()
    # NO: 0.41 * 2.5 + 0.025 = 1.05 capital; 45c EV.
    row["side_economics"][1] = {
        "side": "NO",
        "ask_dollars": "0.4100",
        "quantity": "2.50",
        "modeled_taker_fee_dollars": "0.025",
        "modeled_after_fee_ev_cents": "45",
    }
    row["depth_dollars"] = "1.025"
    row["net_edge_per_dollar"] = str(Decimal("0.45") / Decimal("1.05"))
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_portfolio_metric_binding_verified"] is True
    assert Decimal(out["derived_depth_dollars"]) == Decimal("1.025")


def test_missing_selected_side_economics_fails_closed():
    row = _row()
    row["side_economics"] = []
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_PORTFOLIO_METRICS_UNVERIFIED" in out["blockers"]

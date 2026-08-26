from decimal import Decimal

from src.weather_selected_side_economics_arithmetic_guard import (
    audit_weather_selected_side_economics_arithmetic,
)


def _row():
    return {
        "best_shadow_side": "NO",
        "modeled_after_fee_ev_cents": "12.5",
        "side_economics": [
            {
                "side": "YES",
                "ask_cents": 60,
                "available_contracts": 1,
                "modeled_taker_fee_cents": 2,
                "forecast_side_probability": "0.40",
                "modeled_after_fee_ev_cents": "-22",
            },
            {
                "side": "NO",
                "ask_cents": 41,
                "available_contracts": 1,
                "modeled_taker_fee_cents": "1.5",
                "forecast_side_probability": "0.55",
                "modeled_after_fee_ev_cents": "12.5",
            },
        ],
    }


def test_selected_side_ev_arithmetic_verifies():
    out = audit_weather_selected_side_economics_arithmetic(_row())
    assert out["selected_side_economics_arithmetic_verified"] is True
    assert out["recomputed_after_fee_ev_cents"] == "12.50"
    assert out["recomputed_side_economics"] == {"NO": "12.50", "YES": "-22.00"}
    assert out["recomputed_best_side"] == "NO"
    assert out["blockers"] == []
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_matching_book_shape_cannot_rescue_fabricated_ev():
    row = _row()
    row["side_economics"][1]["modeled_after_fee_ev_cents"] = "40"
    row["modeled_after_fee_ev_cents"] = "40"
    out = audit_weather_selected_side_economics_arithmetic(row)
    assert out["selected_side_economics_arithmetic_verified"] is False
    assert "SELECTED_SIDE_EV_ARITHMETIC_MISMATCH" in out["blockers"]


def test_opposite_side_fabricated_ev_cannot_distort_ranking():
    row = _row()
    # The selected NO record remains internally perfect. Under the old selected-only
    # audit this malformed opposite side was never checked, even though side ranking
    # depends on both records.
    row["side_economics"][0]["modeled_after_fee_ev_cents"] = "-99"
    out = audit_weather_selected_side_economics_arithmetic(row)
    assert out["selected_side_economics_arithmetic_verified"] is False
    assert "OPPOSITE_SIDE_EV_ARITHMETIC_MISMATCH" in out["blockers"]
    assert out["recomputed_best_side"] == "NO"


def test_recomputed_best_side_must_match_declared_selection():
    row = _row()
    row["side_economics"][0].update(
        ask_cents=30,
        modeled_taker_fee_cents=1,
        forecast_side_probability="0.60",
        modeled_after_fee_ev_cents="29",
    )
    out = audit_weather_selected_side_economics_arithmetic(row)
    assert out["selected_side_economics_arithmetic_verified"] is False
    assert out["recomputed_best_side"] == "YES"
    assert "RECOMPUTED_BEST_SIDE_MISMATCH" in out["blockers"]


def test_probability_fee_price_and_quantity_must_be_explicit_and_finite():
    mutations = (
        ("forecast_side_probability", "NaN"),
        ("forecast_side_probability", "1.01"),
        ("modeled_taker_fee_cents", -1),
        ("ask_cents", 0),
        ("available_contracts", 0),
    )
    for key, value in mutations:
        row = _row()
        row["side_economics"][1][key] = value
        out = audit_weather_selected_side_economics_arithmetic(row)
        assert out["selected_side_economics_arithmetic_verified"] is False


def test_top_level_ev_must_match_selected_record():
    row = _row()
    row["modeled_after_fee_ev_cents"] = "13"
    out = audit_weather_selected_side_economics_arithmetic(row)
    assert out["selected_side_economics_arithmetic_verified"] is False
    assert "SELECTED_SIDE_TOP_LEVEL_EV_MISMATCH" in out["blockers"]


def test_fixed_point_selected_side_arithmetic_is_supported():
    row = _row()
    row["modeled_after_fee_ev_cents"] = "15.0"
    row["side_economics"][1] = {
        "side": "NO",
        "ask_dollars": "0.4100",
        "quantity": "2.50",
        "modeled_taker_fee_dollars": "0.025",
        "forecast_side_probability": "0.48",
        "modeled_after_fee_ev_cents": "15.0",
    }
    out = audit_weather_selected_side_economics_arithmetic(row)
    assert out["selected_side_economics_arithmetic_verified"] is True
    assert Decimal(out["recomputed_after_fee_ev_cents"]) == Decimal("15")


def test_selected_side_identity_must_be_unique():
    row = _row()
    row["side_economics"].append(dict(row["side_economics"][1]))
    out = audit_weather_selected_side_economics_arithmetic(row)
    assert out["selected_side_economics_arithmetic_verified"] is False
    assert "SELECTED_SIDE_ECONOMICS_REQUIRED" in out["blockers"]

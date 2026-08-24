from decimal import Decimal

from src.sports_capacity_math import CapacityDirection, CapacityLeg, evaluate_direction, summarize_capacity
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.strict_mlb_prop_snapshot import conservative_kalshi_fee

D = Decimal


def _kalshi_fee(price: Decimal, quantity: Decimal) -> Decimal:
    return conservative_kalshi_fee(int(quantity), price, D("1"))


def _leg(venue: str, side: str, price: str, depth: str, fee_fn):
    return CapacityLeg(venue, f"{venue}-m", side, D(price), D(depth), fee_fn)


def test_capacity_tiers_preserve_quantity_fee_rounding_and_floor():
    direction = CapacityDirection(
        "GEMINI_YES_KALSHI_NO",
        "mlb|example",
        (
            _leg("gemini", "YES", "0.45", "25", gemini_predictions_taker_fee),
            _leg("kalshi", "NO", "0.45", "25", _kalshi_fee),
        ),
    )
    result = evaluate_direction(direction)
    rows = {row["quantity"]: row for row in result["tiers"]}
    assert rows[1]["status"] == "QUALIFIES"
    assert D(rows[1]["locked_profit"]) == D("0.06")
    assert rows[5]["status"] == "QUALIFIES"
    assert D(rows[5]["locked_profit"]) > D("0.15")
    assert rows[25]["status"] == "QUALIFIES"
    assert rows[50]["status"] == "DEPTH_BLOCKED"
    assert result["largest_qualifying_tier"] == 25


def test_capacity_rejects_positive_but_sub_floor_edge():
    zero = lambda price, quantity: D("0")
    direction = CapacityDirection(
        "A_YES_B_NO",
        "key",
        (_leg("a", "YES", "0.49", "100", zero), _leg("b", "NO", "0.49", "100", zero)),
    )
    result = evaluate_direction(direction)
    assert all(row["status"] == "EDGE_BLOCKED" for row in result["tiers"])
    assert result["largest_qualifying_tier"] == 0


def test_summary_labels_snapshot_as_non_realized_capacity():
    zero = lambda price, quantity: D("0")
    good = evaluate_direction(CapacityDirection(
        "GOOD", "k1", (_leg("a", "YES", "0.40", "10", zero), _leg("b", "NO", "0.50", "10", zero))
    ))
    bad = evaluate_direction(CapacityDirection(
        "BAD", "k2", (_leg("a", "YES", "0.50", "10", zero), _leg("b", "NO", "0.49", "10", zero))
    ))
    summary = summarize_capacity([good, bad])
    assert summary["directions_evaluated"] == 2
    assert summary["directions_with_3c_capacity"] == 1
    assert summary["execution_eligible"] is False
    assert "not daily realized capacity" in summary["warning"]

import pytest

from src.polymarket_temporal_arb_shadow import Fill, MarketInventory


def test_two_sided_inventory_locks_profit_after_fees():
    inv = MarketInventory("btc-5m", min_locked_edge_per_pair=0.03)
    inv.add_fill(Fill("UP", 0.25, 10, fee_usd=0.10))
    inv.add_fill(Fill("DOWN", 0.60, 10, fee_usd=0.10))

    snapshot = inv.snapshot()

    assert snapshot["avg_up_all_in"] == pytest.approx(0.26)
    assert snapshot["avg_down_all_in"] == pytest.approx(0.61)
    assert snapshot["pair_cost_all_in"] == pytest.approx(0.87)
    assert snapshot["locked_profit_per_pair"] == pytest.approx(0.13)
    assert snapshot["locked_profit_total"] == pytest.approx(1.30)
    assert snapshot["state"] == "LOCKED_POSITIVE"


def test_one_sided_inventory_is_not_called_arbitrage():
    inv = MarketInventory("eth-15m")
    inv.add_fill(Fill("UP", 0.30, 5))

    snapshot = inv.snapshot()

    assert snapshot["state"] == "ONE_SIDED"
    assert snapshot["matched_qty"] == 0
    assert snapshot["locked_profit_per_pair"] is None
    assert snapshot["residual_side"] == "UP"
    assert snapshot["residual_qty"] == pytest.approx(5)


def test_two_sided_but_expensive_inventory_is_not_locked_positive():
    inv = MarketInventory("btc-15m", min_locked_edge_per_pair=0.03)
    inv.add_fill(Fill("UP", 0.55, 4))
    inv.add_fill(Fill("DOWN", 0.46, 4))

    snapshot = inv.snapshot()

    assert snapshot["pair_cost_all_in"] == pytest.approx(1.01)
    assert snapshot["locked_profit_per_pair"] == pytest.approx(-0.01)
    assert snapshot["state"] == "TWO_SIDED_UNLOCKED"


def test_residual_inventory_is_reported_separately():
    inv = MarketInventory("eth-5m")
    inv.add_fill(Fill("UP", 0.20, 8))
    inv.add_fill(Fill("DOWN", 0.70, 5))

    snapshot = inv.snapshot()

    assert snapshot["matched_qty"] == pytest.approx(5)
    assert snapshot["residual_side"] == "UP"
    assert snapshot["residual_qty"] == pytest.approx(3)


def test_mark_to_fair_separates_directional_ev_from_locked_edge():
    inv = MarketInventory("btc-5m")
    inv.add_fill(Fill("UP", 0.40, 2))
    inv.add_fill(Fill("DOWN", 0.55, 1))

    assert inv.mark_to_fair(0.70) == pytest.approx(0.35)


@pytest.mark.parametrize("probability", [-0.01, 1.01])
def test_mark_to_fair_rejects_invalid_probability(probability):
    inv = MarketInventory("btc-5m")
    with pytest.raises(ValueError):
        inv.mark_to_fair(probability)

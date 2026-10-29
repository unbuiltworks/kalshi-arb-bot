"""Cumulative inventory math: the heart of the window reconstruction."""

from __future__ import annotations

import pytest

from src.polymarket_l5.inventory import InventoryState, apply_trade, final_state, running_states


def trade(outcome, side, price, shares, timestamp=0):
    return {
        "outcome": outcome,
        "side": side,
        "price": price,
        "shares": shares,
        "dollars": round(price * shares, 10),
        "timestamp": timestamp,
    }


def test_single_buy_creates_directional_inventory():
    (row,) = running_states([trade("Up", "BUY", 0.40, 100, 10)])
    assert row["cum_up_qty"] == 100
    assert row["cum_up_spend"] == 40.0
    assert row["wavg_up_price"] == 0.40
    assert row["matched_qty"] == 0
    assert row["matched_pair_unit_cost"] is None
    assert row["excess_qty"] == 100
    assert row["dominant_side"] == "Up"


def test_pairing_produces_matched_quantity_and_pair_cost():
    rows = running_states(
        [trade("Up", "BUY", 0.40, 100, 10), trade("Down", "BUY", 0.55, 100, 20)]
    )
    last = rows[-1]
    assert last["matched_qty"] == 100
    assert last["matched_pair_unit_cost"] == pytest.approx(0.95)
    assert last["matched_pair_cost"] == pytest.approx(95.0)
    assert last["excess_qty"] == 0
    assert last["dominant_side"] == "BALANCED"


def test_weighted_average_price_updates_across_buys():
    rows = running_states(
        [trade("Up", "BUY", 0.40, 100, 10), trade("Up", "BUY", 0.80, 100, 20)]
    )
    assert rows[-1]["wavg_up_price"] == pytest.approx(0.60)
    assert rows[-1]["cum_up_spend"] == pytest.approx(120.0)


def test_partial_pairing_leaves_signed_residual():
    rows = running_states(
        [trade("Up", "BUY", 0.40, 100, 10), trade("Down", "BUY", 0.55, 60, 20)]
    )
    last = rows[-1]
    assert last["matched_qty"] == 60
    assert last["excess_qty"] == 40
    assert last["excess_abs_qty"] == 40
    assert last["dominant_side"] == "Up"
    assert last["excess_cost"] == pytest.approx(16.0)  # 40 shares at the 0.40 average


def test_down_dominant_residual_is_negative():
    rows = running_states(
        [trade("Up", "BUY", 0.40, 50, 10), trade("Down", "BUY", 0.55, 120, 20)]
    )
    assert rows[-1]["excess_qty"] == -70
    assert rows[-1]["dominant_side"] == "Down"


def test_sell_reduces_basis_at_average_cost_and_books_realized_pnl():
    rows = running_states(
        [
            trade("Up", "BUY", 0.40, 100, 10),
            trade("Up", "BUY", 0.60, 100, 20),  # average now 0.50
            trade("Up", "SELL", 0.70, 100, 30),
        ]
    )
    last = rows[-1]
    assert last["cum_up_qty"] == 100
    assert last["wavg_up_price"] == pytest.approx(0.50)
    assert last["cum_realized_pnl_trading"] == pytest.approx(20.0)  # (0.70-0.50)*100


def test_selling_everything_zeroes_the_side_cleanly():
    rows = running_states(
        [trade("Up", "BUY", 0.40, 100, 10), trade("Up", "SELL", 0.45, 100, 20)]
    )
    assert rows[-1]["cum_up_qty"] == 0
    assert rows[-1]["cum_up_spend"] == 0
    assert rows[-1]["wavg_up_price"] == 0
    assert rows[-1]["cum_realized_pnl_trading"] == pytest.approx(5.0)


def test_oversell_is_clamped_and_flagged():
    rows = running_states(
        [trade("Up", "BUY", 0.40, 10, 10), trade("Up", "SELL", 0.50, 25, 20)]
    )
    assert rows[-1]["cum_up_qty"] == 0
    assert "sell_exceeds_held_qty" in rows[-1]["anomalies"]


def test_sequence_tracks_inter_trade_gaps_and_counts():
    rows = running_states(
        [
            trade("Up", "BUY", 0.40, 10, 100),
            trade("Down", "BUY", 0.50, 10, 137),
            trade("Up", "BUY", 0.45, 10, 200),
        ]
    )
    assert [row["trade_seq"] for row in rows] == [1, 2, 3]
    assert [row["seconds_since_prev_trade"] for row in rows] == [None, 37, 63]
    assert rows[-1]["cum_trade_count"] == 3
    assert rows[-1]["cum_gross_dollars_bought"] == pytest.approx(4.0 + 5.0 + 4.5)


def test_unknown_outcome_is_recorded_not_silently_dropped():
    state = InventoryState()
    apply_trade(state, trade("Sideways", "BUY", 0.5, 10))
    assert state.anomalies == ["unmapped_outcome"]
    assert state.up.qty == 0 and state.down.qty == 0


def test_final_state_matches_last_running_state():
    trades = [
        trade("Up", "BUY", 0.40, 100, 10),
        trade("Down", "BUY", 0.52, 80, 20),
        trade("Up", "SELL", 0.60, 30, 30),
    ]
    last = running_states(trades)[-1]
    state = final_state(trades)
    assert state.up.qty == pytest.approx(last["cum_up_qty"])
    assert state.down.qty == pytest.approx(last["cum_down_qty"])
    assert state.matched_qty == pytest.approx(last["matched_qty"])
    assert state.excess_qty == pytest.approx(last["excess_qty"])


def test_matched_pair_cost_above_one_dollar_is_a_locked_loss():
    rows = running_states(
        [trade("Up", "BUY", 0.62, 100, 10), trade("Down", "BUY", 0.55, 100, 20)]
    )
    assert rows[-1]["matched_pair_unit_cost"] == pytest.approx(1.17)
    assert rows[-1]["matched_pair_cost"] == pytest.approx(117.0)

"""Cumulative two-sided inventory math.

Every expected number here is worked by hand in the comment beside it.
"""

import pytest

from src.l5.inventory import BALANCED, DOWN, UP, Inventory, SideState


class TestSideState:
    def test_buys_accumulate_a_weighted_average(self):
        side = SideState()
        side.buy(100, 0.40, 40.0)
        side.buy(300, 0.60, 180.0)
        # 220 spent on 400 shares -> 0.55
        assert side.qty == 400
        assert side.cost == pytest.approx(220.0)
        assert side.avg_price == pytest.approx(0.55)

    def test_average_is_undefined_with_no_position(self):
        assert SideState().avg_price is None

    def test_sell_removes_shares_at_the_basis_before_the_sell(self):
        side = SideState()
        side.buy(100, 0.40, 40.0)
        side.buy(100, 0.60, 60.0)      # basis 0.50 on 200 shares
        side.sell(50, 0.90, 45.0)      # removes 50 * 0.50 = 25 of basis
        assert side.qty == 150
        assert side.cost == pytest.approx(75.0)
        assert side.avg_price == pytest.approx(0.50), "a sale must not move the basis"
        assert side.sell_proceeds == pytest.approx(45.0)

    def test_gross_buy_average_ignores_later_sells(self):
        side = SideState()
        side.buy(100, 0.40, 40.0)
        side.sell(100, 0.90, 90.0)
        assert side.avg_price is None          # nothing held
        assert side.gross_avg_buy_price == pytest.approx(0.40)

    def test_selling_flat_clears_the_basis(self):
        side = SideState()
        side.buy(100, 0.40, 40.0)
        side.sell(100, 0.50, 50.0)
        assert side.qty == pytest.approx(0.0)
        assert side.cost == pytest.approx(0.0)
        assert side.oversold is False

    def test_selling_more_than_held_is_flagged_not_silently_absorbed(self):
        side = SideState()
        side.buy(100, 0.40, 40.0)
        side.sell(150, 0.50, 75.0)
        assert side.oversold is True
        assert side.qty == pytest.approx(-50.0)
        assert side.avg_price is None


class TestInventorySnapshots:
    def test_one_sided_position_has_no_match(self):
        inv = Inventory()
        snap = inv.apply(UP, "BUY", 100, 0.40, 40.0)
        assert snap.up_qty == 100 and snap.down_qty == 0
        assert snap.matched_qty == 0
        assert snap.matched_pair_cost_per_share is None
        assert snap.matched_pair_cost_total == 0.0
        assert snap.dominant_side == UP
        assert snap.excess_qty == 100

    def test_matched_pair_cost_is_the_sum_of_the_two_averages(self):
        inv = Inventory()
        inv.apply(UP, "BUY", 100, 0.40, 40.0)
        snap = inv.apply(DOWN, "BUY", 100, 0.55, 55.0)
        assert snap.matched_qty == 100
        assert snap.matched_pair_cost_per_share == pytest.approx(0.95)
        assert snap.matched_pair_cost_total == pytest.approx(95.0)
        assert snap.dominant_side == BALANCED
        assert snap.excess_qty == pytest.approx(0.0)

    def test_matched_quantity_is_the_smaller_leg(self):
        inv = Inventory()
        inv.apply(UP, "BUY", 300, 0.40, 120.0)
        snap = inv.apply(DOWN, "BUY", 100, 0.55, 55.0)
        assert snap.matched_qty == 100
        # per-share pair cost still uses both averages
        assert snap.matched_pair_cost_per_share == pytest.approx(0.95)
        assert snap.matched_pair_cost_total == pytest.approx(95.0)
        assert snap.excess_qty == 200 and snap.excess_side == UP

    def test_dominant_side_flips_as_the_book_tilts(self):
        inv = Inventory()
        assert inv.apply(UP, "BUY", 100, 0.40, 40.0).dominant_side == UP
        assert inv.apply(DOWN, "BUY", 150, 0.55, 82.5).dominant_side == DOWN
        assert inv.apply(UP, "BUY", 50, 0.45, 22.5).dominant_side == BALANCED

    def test_running_totals_track_cash_in_and_out(self):
        inv = Inventory()
        inv.apply(UP, "BUY", 100, 0.40, 40.0)
        inv.apply(DOWN, "BUY", 100, 0.55, 55.0)
        snap = inv.apply(UP, "SELL", 50, 0.70, 35.0)
        assert snap.cum_trades == 3
        assert snap.cum_dollars_bought == pytest.approx(95.0)
        assert snap.cum_dollars_sold == pytest.approx(35.0)
        assert snap.cum_net_cash_out == pytest.approx(60.0)

    def test_a_full_four_trade_window_end_to_end(self):
        """The fixture window, worked by hand.

        UP:   100 @ .42 = 42.00, then 200 @ .72 = 144.00
              -> 300 shares, 186.00, avg 0.62
        DOWN: 100 @ .55 = 55.00, then 200 @ .91 = 182.00
              -> 300 shares, 237.00, avg 0.79
        matched 300, pair cost 1.41/share, 423.00 total, balanced.
        """
        inv = Inventory()
        inv.apply(UP, "BUY", 100, 0.42, 42.0)
        inv.apply(DOWN, "BUY", 100, 0.55, 55.0)
        inv.apply(UP, "BUY", 200, 0.72, 144.0)
        snap = inv.apply(DOWN, "BUY", 200, 0.91, 182.0)

        assert snap.up_qty == 300 and snap.up_spend == pytest.approx(186.0)
        assert snap.up_avg_price == pytest.approx(0.62)
        assert snap.down_qty == 300 and snap.down_spend == pytest.approx(237.0)
        assert snap.down_avg_price == pytest.approx(0.79)
        assert snap.matched_qty == 300
        assert snap.matched_pair_cost_per_share == pytest.approx(1.41)
        assert snap.matched_pair_cost_total == pytest.approx(423.0)
        assert snap.excess_qty == pytest.approx(0.0)
        assert snap.dominant_side == BALANCED
        assert snap.cum_dollars_bought == pytest.approx(423.0)
        assert snap.oversold is False

    def test_oversold_propagates_to_the_snapshot(self):
        inv = Inventory()
        inv.apply(UP, "BUY", 100, 0.40, 40.0)
        snap = inv.apply(UP, "SELL", 200, 0.50, 100.0)
        assert snap.oversold is True
        assert snap.matched_qty == 0

    def test_rejects_unknown_outcomes_and_actions(self):
        inv = Inventory()
        with pytest.raises(ValueError):
            inv.apply("SIDEWAYS", "BUY", 1, 0.5, 0.5)
        with pytest.raises(ValueError):
            inv.apply(UP, "TRANSFER", 1, 0.5, 0.5)

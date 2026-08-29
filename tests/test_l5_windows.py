"""Window grouping, per-window summarisation and realized-P&L attachment."""

import pytest

from src.l5.normalize import build_market_index, normalize
from src.l5.windows import build_pnl_index, build_windows, group_trades, price_band
from tests import l5_fixtures as fx


@pytest.fixture
def index():
    return build_market_index(fx.ALL_MARKETS)


def _trades(index, records):
    trades, _ = normalize({"activity": records}, index)
    return trades


class TestPriceBands:
    @pytest.mark.parametrize(
        "price,expected",
        [
            (0.01, "lt70"), (0.699, "lt70"),
            (0.70, "70to80"), (0.799, "70to80"),
            (0.80, "80to90"), (0.899, "80to90"),
            (0.90, "gte90"), (0.99, "gte90"), (1.0, "gte90"),
        ],
    )
    def test_band_edges_are_closed_below_open_above(self, price, expected):
        assert price_band(price) == expected


class TestGrouping:
    def test_groups_by_condition_id_not_by_title(self, index):
        records = fx.simple_window_activity() + [
            # Same asset, same length, near-identical slug -- different window.
            fx.trade("0xbtc5m02", "Up", "BUY", 0.5, 10, fx.BTC5M_END + 30, "0xb1"),
        ]
        grouped = group_trades(_trades(index, records))
        assert set(grouped) == {"0xbtc5m01", "0xbtc5m02"}
        assert len(grouped["0xbtc5m01"]) == 4
        assert len(grouped["0xbtc5m02"]) == 1

    def test_each_group_is_chronological(self, index):
        records = list(reversed(fx.simple_window_activity()))
        grouped = group_trades(_trades(index, records))
        stamps = [t.timestamp for t in grouped["0xbtc5m01"]]
        assert stamps == sorted(stamps)

    def test_windows_come_back_in_window_start_order(self, index):
        records = [
            fx.trade("0xeth15m01", "Up", "BUY", 0.5, 10, fx.ETH15M_START + 10, "0xe1"),
            fx.trade("0xbtc5m01", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 10, "0xb1"),
        ]
        windows = build_windows(_trades(index, records))
        assert [w.condition_id for w in windows] == ["0xbtc5m01", "0xeth15m01"]


class TestSummary:
    @pytest.fixture
    def summary(self, index):
        windows = build_windows(_trades(index, fx.simple_window_activity()))
        assert len(windows) == 1
        return windows[0].summary

    def test_identity_and_geometry(self, summary):
        assert summary.asset == "BTC"
        assert summary.window_minutes == 5
        assert summary.window_start_ts == fx.BTC5M_START
        assert summary.window_end_ts == fx.BTC5M_END
        assert summary.window_source == "metadata"

    def test_opening_trade(self, summary):
        assert summary.first_side == "UP"
        assert summary.first_price == pytest.approx(0.42)
        assert summary.first_action == "BUY"
        assert summary.first_seconds_from_window_start == 30
        assert summary.first_price_below_half is True

    def test_totals(self, summary):
        assert summary.total_trades == 4
        assert summary.total_buys == 4 and summary.total_sells == 0
        assert summary.total_dollars_bought == pytest.approx(423.0)
        assert summary.total_shares_bought == pytest.approx(600.0)

    def test_price_extremes_and_escalation_flags(self, summary):
        assert summary.max_price_bought == pytest.approx(0.91)
        assert summary.min_price_bought == pytest.approx(0.42)
        assert summary.bought_at_70c_plus is True
        assert summary.bought_at_80c_plus is True
        assert summary.bought_at_90c_plus is True

    def test_quantity_and_dollars_by_band(self, summary):
        # .42 -> lt70 (100), .55 -> lt70 (100), .72 -> 70to80 (200), .91 -> gte90 (200)
        assert summary.qty_lt70 == pytest.approx(200.0)
        assert summary.qty_70to80 == pytest.approx(200.0)
        assert summary.qty_80to90 == pytest.approx(0.0)
        assert summary.qty_gte90 == pytest.approx(200.0)
        assert summary.dollars_lt70 == pytest.approx(97.0)
        assert summary.dollars_70to80 == pytest.approx(144.0)
        assert summary.dollars_gte90 == pytest.approx(182.0)

    def test_final_inventory_and_matched_pair(self, summary):
        assert summary.final_up_qty == pytest.approx(300.0)
        assert summary.final_down_qty == pytest.approx(300.0)
        assert summary.final_up_avg_price == pytest.approx(0.62)
        assert summary.final_down_avg_price == pytest.approx(0.79)
        assert summary.matched_qty == pytest.approx(300.0)
        assert summary.matched_pair_cost_per_share == pytest.approx(1.41)
        assert summary.matched_pair_cost_total == pytest.approx(423.0)

    def test_finishes_balanced(self, summary):
        assert summary.residual_qty == pytest.approx(0.0)
        assert summary.residual_side == "BALANCED"
        assert summary.finished_balanced is True

    def test_time_to_the_opposite_side(self, summary):
        assert summary.traded_both_sides is True
        assert summary.seconds_to_opposite_side == 15   # +30s then +45s
        assert summary.trades_before_opposite_side == 1

    def test_pnl_absent_without_position_rows(self, summary):
        assert summary.realized_pnl is None
        assert summary.pnl_source == "unavailable"


class TestDirectionalWindow:
    def test_one_sided_window_is_directional(self, index):
        records = [
            fx.trade("0xbtc5m01", "Up", "BUY", 0.35, 500, fx.BTC5M_START + 20, "0xd1"),
        ]
        summary = build_windows(_trades(index, records))[0].summary
        assert summary.traded_both_sides is False
        assert summary.seconds_to_opposite_side is None
        assert summary.finished_balanced is False
        assert summary.residual_side == "UP"
        assert summary.residual_frac_of_larger_leg == pytest.approx(1.0)
        assert summary.matched_qty == 0

    def test_small_residual_counts_as_balanced(self, index):
        records = [
            fx.trade("0xbtc5m01", "Up", "BUY", 0.45, 1000, fx.BTC5M_START + 20, "0xd1"),
            fx.trade("0xbtc5m01", "Down", "BUY", 0.55, 990, fx.BTC5M_START + 40, "0xd2"),
        ]
        summary = build_windows(_trades(index, records))[0].summary
        assert summary.residual_qty == pytest.approx(10.0)
        assert summary.finished_balanced is True, "1% residual is within tolerance"

    def test_large_residual_counts_as_directional(self, index):
        records = [
            fx.trade("0xbtc5m01", "Up", "BUY", 0.45, 1000, fx.BTC5M_START + 20, "0xd1"),
            fx.trade("0xbtc5m01", "Down", "BUY", 0.55, 500, fx.BTC5M_START + 40, "0xd2"),
        ]
        summary = build_windows(_trades(index, records))[0].summary
        assert summary.finished_balanced is False
        assert summary.residual_frac_of_larger_leg == pytest.approx(0.5)


class TestRealizedPnl:
    def test_sums_both_tokens_of_a_condition(self):
        index = build_pnl_index([
            fx.position("0xbtc5m01", "tok_up", 12.5),
            fx.position("0xbtc5m01", "tok_down", -4.5),
            fx.position("0xeth15m01", "tok_up", 3.0),
        ])
        assert index["0xbtc5m01"]["realized_pnl"] == pytest.approx(8.0)
        assert index["0xbtc5m01"]["rows"] == 2
        assert index["0xeth15m01"]["realized_pnl"] == pytest.approx(3.0)

    def test_attaches_to_the_window_summary(self, index):
        pnl = build_pnl_index([
            fx.position("0xbtc5m01", "tok_up", 12.5),
            fx.position("0xbtc5m01", "tok_down", -4.5),
        ])
        summary = build_windows(_trades(index, fx.simple_window_activity()), pnl)[0].summary
        assert summary.realized_pnl == pytest.approx(8.0)
        assert summary.pnl_source == "polymarket_positions.realizedPnl"
        assert summary.pnl_rows == 2

    def test_rows_without_pnl_fields_stay_unavailable(self, index):
        pnl = build_pnl_index([{"conditionId": "0xbtc5m01", "size": 10}])
        summary = build_windows(_trades(index, fx.simple_window_activity()), pnl)[0].summary
        assert summary.realized_pnl is None
        assert summary.pnl_source == "unavailable"

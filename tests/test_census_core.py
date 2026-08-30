"""Census math: sequential ceilings, caps, q-awareness, aggregation."""

import pytest

from src.census.core import (
    DOWN,
    UP,
    TapeTrade,
    aggregate,
    census_window,
    sequential_ceiling,
)


def T(ts, side, count, up=0.5, down=0.5):
    return TapeTrade(ts=ts, maker_side=side, count=count, up_price=up, down_price=down)


class TestSequentialCeiling:
    def test_uncapped_ceiling_is_min_of_side_flows(self):
        tape = [T(1, UP, 100), T(2, DOWN, 60), T(3, UP, 40)]
        r = sequential_ceiling(tape)
        assert r.up_qty == 140 and r.down_qty == 60
        assert r.matched == 60
        assert r.residual == 80

    def test_q_caps_absorption_per_event_not_per_window(self):
        # One 140-lot sweep: a q=10 maker takes 10, not 140.
        tape = [T(1, UP, 140), T(2, DOWN, 140)]
        r = sequential_ceiling(tape, q=10)
        assert r.up_qty == 10 and r.down_qty == 10
        assert r.matched == 10

    def test_residual_cap_blocks_one_sided_accumulation(self):
        tape = [T(i, UP, 50) for i in range(1, 6)]  # 250 one-sided
        r = sequential_ceiling(tape, residual_cap=25)
        assert r.up_qty == 25
        assert r.residual == 25
        assert r.matched == 0

    def test_residual_cap_allows_partial_absorption_of_a_large_clip(self):
        r = sequential_ceiling([T(1, UP, 100)], residual_cap=30)
        assert r.up_qty == 30

    def test_balance_restoring_flow_is_never_blocked(self):
        tape = [T(1, UP, 25), T(2, DOWN, 25), T(3, UP, 25), T(4, DOWN, 25)]
        r = sequential_ceiling(tape, residual_cap=25)
        assert r.matched == 50
        assert r.residual == 0

    def test_order_matters_burst_then_reverse_vs_alternating(self):
        # Same totals, different order: alternating pairs fully under a tight
        # cap; bursty flow loses the excess to the cap.
        alternating = [T(i, UP if i % 2 else DOWN, 10) for i in range(1, 21)]
        bursty = [T(i, UP, 10) for i in range(1, 11)] + [T(i, DOWN, 10) for i in range(11, 21)]
        r_alt = sequential_ceiling(alternating, residual_cap=10)
        r_burst = sequential_ceiling(bursty, residual_cap=10)
        assert r_alt.matched == 100
        assert r_burst.matched == 10, "cap admits only 10 of the burst, so only 10 can match"
        assert r_alt.matched > r_burst.matched

    def test_pair_cost_and_gross_come_from_tape_prices(self):
        tape = [T(1, UP, 100, up=0.40, down=0.60), T(2, DOWN, 100, up=0.45, down=0.55)]
        r = sequential_ceiling(tape)
        assert r.pair_cost == pytest.approx(0.95)
        assert r.gross_locked == pytest.approx(100 * 0.05)

    def test_adjusted_ev_subtracts_lambda_times_residual(self):
        tape = [T(1, UP, 100, up=0.40, down=0.60), T(2, DOWN, 50, up=0.45, down=0.55)]
        r = sequential_ceiling(tape)
        assert r.residual == 50
        assert r.adjusted_ev(0.12) == pytest.approx(r.gross_locked - 6.0)

    def test_empty_tape(self):
        r = sequential_ceiling([])
        assert r.matched == 0 and r.residual == 0 and r.pair_cost is None


class TestWindowCensus:
    def test_descriptive_metrics(self):
        tape = [T(1, UP, 10), T(2, DOWN, 30), T(3, DOWN, 30), T(4, UP, 10)]
        w = census_window("w1", "test", tape)
        assert w.trades == 4 and w.contracts == 80
        assert w.both_sides is True
        assert w.alternation_rate == pytest.approx(2 / 3)
        assert w.clip_median == 20.0
        # 2 q-values x 4 caps
        assert len(w.ceilings) == 8

    def test_one_sided_window(self):
        w = census_window("w2", "test", [T(1, UP, 10)])
        assert w.both_sides is False
        assert w.alternation_rate is None

    def test_tape_is_sorted_before_walking(self):
        # Out-of-order input must not change the cap dynamics.
        tape = [T(3, UP, 25), T(1, UP, 25), T(2, DOWN, 25)]
        w = census_window("w3", "t", tape, residual_caps=(25,), q_values=(None,))
        assert w.ceilings[0]["matched"] == 25


class TestAggregate:
    def test_sums_by_config_and_carries_benchmarks(self):
        tapes = {
            "a": [T(1, UP, 20), T(2, DOWN, 20)],
            "b": [T(1, UP, 50)],
        }
        windows = [census_window(k, k, v, q_values=(10.0,), residual_caps=(10.0,))
                   for k, v in tapes.items()]
        agg = aggregate(windows)
        assert agg["windows_with_trades"] == 2
        config = agg["by_config"]["q=10.0_R=10.0"]
        assert config["matched"] == 10.0        # a: 10+10 matched at q=10
        assert config["residual"] == 10.0       # b: capped at 10
        assert config["matched_to_residual"] == 1.0
        assert agg["benchmarks"]["l5_polymarket_realized"]["matched_to_residual"] == 4.38

    def test_empty(self):
        agg = aggregate([])
        assert agg["windows_seen"] == 0

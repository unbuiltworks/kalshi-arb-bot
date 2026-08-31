from decimal import Decimal as D

from src.gemini_maker.live_10_canary import (
    FlowGauge,
    MAX_WINDOW_SPEND,
    after_fee_pair_edge,
    can_start_pair,
    entry_quotes,
    recycle_delay,
)


def test_flow_gauge_requires_causal_observation_then_tiers():
    g = FlowGauge()
    g.add(D("100"), now=0.0)
    g.add(D("109"), now=5.0)
    assert g.state() == "DEAD"
    g.add(D("111"), now=6.0)
    assert g.observed_flow() == D("11")
    assert g.state() == "WARM"
    g.add(D("205"), now=10.0)
    assert g.state() == "HOT"
    g.add(D("700"), now=20.0)
    assert g.state() == "VERY_HOT"


def test_warm_quotes_one_cent_back_and_positive_after_fees():
    plan = entry_quotes(D("0.47"), D("0.50"), "WARM")
    assert plan is not None
    yes, no, edge = plan
    assert yes == D("0.46")
    assert no == D("0.49")
    assert edge == after_fee_pair_edge(yes, no)
    assert edge >= 0


def test_hot_joins_bid_when_after_fee_safe():
    plan = entry_quotes(D("0.47"), D("0.50"), "HOT")
    assert plan is not None
    yes, no, edge = plan
    assert yes == D("0.47")
    assert no == D("0.50")
    assert edge >= 0


def test_hot_refuses_one_cent_spread_that_loses_after_current_maker_fees():
    assert entry_quotes(D("0.49"), D("0.50"), "HOT") is None


def test_dead_never_starts_and_final_minute_never_starts():
    assert not can_start_pair(D("0"), 200.0, "DEAD")
    assert not can_start_pair(D("0"), 60.0, "HOT")
    assert not can_start_pair(D("0"), 30.0, "VERY_HOT")


def test_ten_dollars_is_hard_ceiling_with_full_one_dollar_pair_reserve():
    assert MAX_WINDOW_SPEND == D("10.00")
    assert can_start_pair(D("9.00"), 200.0, "HOT")
    assert not can_start_pair(D("9.01"), 200.0, "HOT")
    assert not can_start_pair(D("10.00"), 200.0, "HOT")


def test_live_scaling_is_q1_recycling_not_large_initial_tranche():
    # The live canary's scaling lever is recycle cadence. Every execution helper
    # submits quantity=1; hotter flow merely earns a faster next q1 pair.
    assert recycle_delay("WARM") == 3.0
    assert recycle_delay("HOT") == 1.0
    assert recycle_delay("VERY_HOT") == 0.5

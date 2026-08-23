# Fresh Railway source/config marker for the v2 production gate.
from decimal import Decimal

from src import mlb_prop_final_lag_live_q1_entry_v2 as v2

D = Decimal


def test_threshold_reached_yes_is_eligible_but_no_is_not():
    assert v2.eligible_decision_state("THRESHOLD_REACHED_STABLE_RESEARCH_ONLY", "YES")
    assert not v2.eligible_decision_state("THRESHOLD_REACHED_STABLE_RESEARCH_ONLY", "NO")
    assert v2.eligible_decision_state("FINAL_STAT_STABLE_RESEARCH_ONLY", "YES")
    assert v2.eligible_decision_state("FINAL_STAT_STABLE_RESEARCH_ONLY", "NO")


def test_live_threshold_only_prices_highest_reached_boundary():
    assert v2.boundary_contract(
        state="THRESHOLD_REACHED_STABLE_RESEARCH_ONLY",
        outcome="YES",
        statistic=2,
        threshold=D("2"),
    )
    assert not v2.boundary_contract(
        state="THRESHOLD_REACHED_STABLE_RESEARCH_ONLY",
        outcome="YES",
        statistic=2,
        threshold=D("1"),
    )


def test_final_prices_nearest_winner_and_loser_boundaries():
    assert v2.boundary_contract(
        state="FINAL_STAT_STABLE_RESEARCH_ONLY", outcome="YES", statistic=2, threshold=D("2")
    )
    assert v2.boundary_contract(
        state="FINAL_STAT_STABLE_RESEARCH_ONLY", outcome="NO", statistic=2, threshold=D("3")
    )
    assert not v2.boundary_contract(
        state="FINAL_STAT_STABLE_RESEARCH_ONLY", outcome="NO", statistic=2, threshold=D("4")
    )


def test_current_polymarket_taker_fee_uses_six_percent_theta():
    assert v2.polymarket_taker_fee_current(1, D("0.50")) == D("0.02")
    assert v2.polymarket_taker_fee_current(1, D("0.20")) == D("0.01")


def test_v2_installs_paced_boundary_and_fast_revalidation():
    assert v2.impl.decided._price_candidate is v2._price_boundary_paced
    assert v2.impl._fresh_edge is v2._fresh_edge_fast
    assert v2.impl.FinalLagLiveHandler._handle is v2._handle_live_threshold
    assert v2.impl.CUMULATIVE_BUDGET >= D("5")
    assert v2.impl.MIN_PROFIT >= D("0.10")

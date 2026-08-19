from decimal import Decimal

from src.sports_gk_autonomous_supervisor import (
    ALL_FAMILIES,
    HITS,
    HOME_RUNS,
    STRIKEOUTS,
    TOTAL_BASES,
    _hard_halt_reason,
)
from src.sports_live_budget_guard import budget_used_from_trades

D = Decimal


def test_unified_supervisor_covers_four_core_prop_families():
    assert ALL_FAMILIES == {HITS, HOME_RUNS, TOTAL_BASES, STRIKEOUTS}


def test_budget_counts_active_reservation_when_no_fill_exists():
    rows = [{
        "strategy": "sports_gk_autonomous_rule_risk",
        "mode": "LIVE_PROBE",
        "status": "CAPITAL_RESERVED",
        "capital_filled": "0",
        "metadata": {"reserved_usd": "0.94"},
    }]
    assert budget_used_from_trades(rows) == D("0.94")


def test_budget_uses_max_of_actual_fill_and_reservation_not_both():
    rows = [{
        "strategy": "sports_gk_autonomous_rule_risk",
        "mode": "LIVE_PROBE",
        "status": "AWAITING_SETTLEMENT",
        "capital_filled": "0.93",
        "metadata": {"reserved_usd": "0.95"},
    }]
    assert budget_used_from_trades(rows) == D("0.95")


def test_released_unfilled_reservation_frees_budget():
    rows = [{
        "strategy": "sports_gk_autonomous_rule_risk",
        "mode": "LIVE_PROBE",
        "status": "CAPITAL_RELEASED",
        "capital_filled": "0",
        "metadata": {"reserved_usd": "0.95"},
    }]
    assert budget_used_from_trades(rows) == D("0")


def test_actual_fill_remains_counted_even_after_reservation_release_marker():
    rows = [{
        "strategy": "sports_gk_autonomous_rule_risk",
        "mode": "LIVE_PROBE",
        "status": "CAPITAL_RELEASED",
        "capital_filled": "0.47",
        "metadata": {"reserved_usd": "0.95"},
    }]
    assert budget_used_from_trades(rows) == D("0.47")


def test_shadow_and_non_sports_trades_do_not_consume_live_sports_budget():
    rows = [
        {"strategy": "sports_gk_x", "mode": "SHADOW", "capital_filled": "10", "metadata": {}},
        {"strategy": "crypto_gk_live", "mode": "LIVE_PROBE", "capital_filled": "10", "metadata": {}},
    ]
    assert budget_used_from_trades(rows) == D("0")


def test_ambiguous_or_one_leg_execution_halts_future_submissions():
    assert _hard_halt_reason([{
        "trade_id": "t1",
        "strategy": "sports_gk_autonomous_rule_risk",
        "mode": "LIVE_PROBE",
        "status": "AMBIGUOUS_SUBMISSION",
        "metadata": {},
    }])
    assert _hard_halt_reason([{
        "trade_id": "t2",
        "strategy": "sports_gk_autonomous_rule_risk",
        "mode": "LIVE_PROBE",
        "status": "AWAITING_SETTLEMENT",
        "metadata": {"execution": {"hard_halt_required": True}},
    }])


def test_clean_or_realized_trade_does_not_trigger_hard_halt():
    assert _hard_halt_reason([{
        "trade_id": "t3",
        "strategy": "sports_gk_autonomous_rule_risk",
        "mode": "LIVE_PROBE",
        "status": "SETTLED_COMPATIBLE",
        "metadata": {"execution": {"hard_halt_required": False}},
    }]) is None

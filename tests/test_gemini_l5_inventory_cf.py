from decimal import Decimal as D

from src import gemini_l5_inventory_cf as invcf
from src.gemini_l5_inventory_cf import (
    _continuous_cycles, _decision_gain, _inventory_path, _ladder_prices, summarize,
)


def test_inventory_path_tracks_repeated_matching_and_residual_flip():
    fills = [
        {"t": 1, "side": "YES", "px": "0.30", "qty": "1", "relation": "open"},
        {"t": 2, "side": "YES", "px": "0.25", "qty": "1", "relation": "extend"},
        {"t": 3, "side": "NO", "px": "0.60", "qty": "1", "relation": "complete"},
        {"t": 4, "side": "NO", "px": "0.55", "qty": "2", "relation": "complete"},
    ]
    path = _inventory_path(fills)
    assert path[1]["residual"] == "2"
    assert path[2]["matched"] == "1"
    assert path[2]["residual"] == "1"
    assert path[3]["matched"] == "2"
    assert path[3]["residual"] == "-1"
    assert path[3]["residual_sign_flip"] is True
    assert path[3]["liquidity_role"] == "maker"
    assert path[3]["accounting_stage"] == "SHADOW"
    assert path[3]["realized"] is False
    assert path[3]["matched_fraction"] == str(D("4") / D("5"))
    assert path[3]["maker_fees_modeled_cumulative"] is not None
    assert path[3]["rebates_observed_cumulative"] is None


def test_gain_per_unit_is_held_mark_minus_running_average_not_a_rule():
    yes = {
        "inventory_before": {"residual": "2", "avg_up": "0.40", "avg_dn": None},
    }
    no = {
        "inventory_before": {"residual": "-2", "avg_up": None, "avg_dn": "0.35"},
    }
    row = {"bid": "0.47", "ask": "0.58"}
    assert _decision_gain(yes, row) == D("0.07")
    # NO maker mark is 1 - YES ask = 0.42.
    assert _decision_gain(no, row) == D("0.07")


def test_ladder_prices_steps_inside_spread_to_max_safe():
    arms = _ladder_prices(D("0.40"), D("0.72"))
    assert arms[0] == ("touch", D("0.40"))
    assert arms[-1] == ("max_fee_safe_post_only", D("0.72"))
    assert ("inside_25", D("0.48")) in arms
    assert ("inside_50", D("0.56")) in arms
    assert ("inside_75", D("0.64")) in arms


def test_ladder_does_not_invent_more_aggressive_price_when_max_safe_below_touch():
    arms = _ladder_prices(D("0.50"), D("0.47"))
    assert arms == [("touch", D("0.50")), ("max_fee_safe_post_only", D("0.47"))]


def test_continuous_cycles_restart_after_flat_match_on_remaining_tape(monkeypatch):
    calls = []
    responses = [
        {"status": "MATCHED", "stop_reason": "flat_complete",
         "fills": [{"t": 20, "side": "YES", "px": "0.30", "qty": "1"},
                   {"t": 30, "side": "NO", "px": "0.60", "qty": "1"}],
         "inventory": {"matched": "1", "residual": "0"}},
        {"status": "MATCHED", "stop_reason": "flat_complete",
         "fills": [{"t": 50, "side": "NO", "px": "0.25", "qty": "1"},
                   {"t": 60, "side": "YES", "px": "0.65", "qty": "1"}],
         "inventory": {"matched": "1", "residual": "0"}},
        {"status": "NO_FILL", "stop_reason": "end", "fills": [],
         "inventory": {"matched": "0", "residual": "0"}},
    ]

    def fake_replay(symbol, rows, prints, **kwargs):
        calls.append([r["t"] for r in rows])
        return responses[len(calls) - 1]

    monkeypatch.setattr(invcf.gs, "replay_window", fake_replay)
    rows = [{"t": t} for t in (10, 20, 30, 40, 50, 60, 70)]
    cycles = _continuous_cycles("GEMI-BTC05M2609131200-UP", rows, [], model={}, art=None)
    assert len(cycles) == 3
    assert calls[0][0] == 10
    assert calls[1][0] == 40
    assert calls[2][0] == 70
    assert cycles[0]["cycle"] == 1
    assert cycles[1]["cycle"] == 2


def test_summary_separates_5m_and_15m_and_action_conversion():
    records = [
        {
            "symbol": "GEMI-BTC05M2609131200-UP", "horizon": "5m",
            "fills": 2, "decisions": 4, "switches": 1, "runs": 2,
            "inter_fill_gap_s_p50": 3.0,
            "canonical_decisions": [{}, {}, {}, {}],
            "canonical_orders": [
                {"relation": "extend", "filled": True},
                {"relation": "complete", "filled": False},
            ],
            "inventory_path": [{"side": "YES"}, {"side": "NO"}],
            "decision_ladders": [{"favorable_gain_state": True}],
            "final_inventory": {"matched_fraction": "1", "residual": "0"},
            "cycle_results": [{"pair_cost_after_fees": "0.97"}],
            "pair_pnl_theoretical": "0.02",
            "residual_settlement_pnl_theoretical": "0",
            "maker_fees_modeled": "0.01",
            "gross_spend_theoretical": "1.00",
        },
        {
            "symbol": "GEMI-BTC15M2609131215-UP", "horizon": "15m",
            "fills": 1, "decisions": 2, "switches": 0, "runs": 1,
            "inter_fill_gap_s_p50": None,
            "canonical_decisions": [{}, {}],
            "canonical_orders": [{"relation": "extend", "filled": False}],
            "inventory_path": [{"side": "YES"}],
            "decision_ladders": [{"favorable_gain_state": False}],
            "final_inventory": {"matched_fraction": "0", "residual": "1"},
            "cycle_results": [],
            "pair_pnl_theoretical": "0",
            "residual_settlement_pnl_theoretical": "-0.10",
            "maker_fees_modeled": "0.01",
            "gross_spend_theoretical": "0.40",
        },
    ]
    out = summarize(records)
    assert out["by_horizon"]["5m"]["fills_per_action"] == 0.5
    assert out["by_horizon"]["5m"]["complete_conversion_pct"] == 0.0
    assert out["by_horizon"]["5m"]["extend_conversion_pct"] == 100.0
    assert out["by_horizon"]["5m"]["favorable_gain_states_per_window"] == 1.0
    assert out["by_horizon"]["15m"]["terminal_residual_abs_p50"] == 1.0
    assert out["by_horizon"]["15m"]["settled_realized_net_usd_per_window"] is None

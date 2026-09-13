from decimal import Decimal as D

from src import gemini_l5_inventory_cf as invcf
from src.gemini_l5_inventory_cf import _continuous_cycles, _inventory_path, _ladder_prices


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

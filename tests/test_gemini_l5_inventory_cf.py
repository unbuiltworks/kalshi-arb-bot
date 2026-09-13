from decimal import Decimal as D

from src.gemini_l5_inventory_cf import _inventory_path, _ladder_prices


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

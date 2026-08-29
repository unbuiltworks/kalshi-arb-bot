# Focused regression coverage for the read-only realtime l5 mirror.
from src.l5_realtime_shadow_mirror import best_prices, inventory_summary, is_target_trade, shadow_decision


def test_best_prices():
    bid, ask = best_prices({"bids": [{"price": "0.40"}, {"price": "0.42"}], "asks": [{"price": "0.60"}, {"price": "0.58"}]})
    assert bid == 0.42
    assert ask == 0.58


def test_inventory_builds_matched_pairs_and_residual():
    fills = [
        {"outcome": "Up", "side": "BUY", "size": 100, "price": 0.60},
        {"outcome": "Down", "side": "BUY", "size": 80, "price": 0.30},
    ]
    out = inventory_summary(fills)
    assert out["matched_qty"] == 80
    assert out["matched_avg_pair_cost"] == 0.90
    assert out["unmatched_up"] == 20
    assert out["unmatched_down"] == 0


def test_shadow_decision_trades_when_spot_move_and_edge_are_large():
    snap = {"up_ask": 0.60, "down_ask": 0.42, "spot": 101.0, "window_ref_spot": 100.0}
    out = shadow_decision(snap, {})
    assert out["action"] == "PAPER_BUY"
    assert out["side"] == "UP"
    assert out["edge"] >= 0.03


def test_shadow_decision_no_trade_without_context():
    assert shadow_decision(None, {})["action"] == "NO_TRADE"


def test_target_trade_filter():
    assert is_target_trade({"title": "Bitcoin Up or Down - 15m"})
    assert is_target_trade({"eventSlug": "eth-updown-15m-123"})
    assert not is_target_trade({"title": "Will the Phillies win?"})

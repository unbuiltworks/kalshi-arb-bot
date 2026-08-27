from src.polymarket_maker_econ_shadow import (
    RewardConfig,
    choose_resting_bid,
    order_score,
    two_sided_raw_score,
)


def test_choose_resting_bid_requires_non_crossed_book():
    assert choose_resting_bid({"best_bid": 0.49, "best_ask": 0.51}) == 0.49
    assert choose_resting_bid({"best_bid": 0.51, "best_ask": 0.51}) is None


def test_order_score_tighter_is_better_and_outside_is_zero():
    tight = order_score(max_spread_cents=3, midpoint=0.50, price=0.49, size=100)
    wide = order_score(max_spread_cents=3, midpoint=0.50, price=0.48, size=100)
    outside = order_score(max_spread_cents=3, midpoint=0.50, price=0.46, size=100)
    assert tight > wide > 0
    assert outside == 0


def test_two_sided_score_uses_weaker_side_and_min_size():
    cfg = RewardConfig("c", max_spread_cents=3, min_size=10, total_daily_rate=100)
    score, eligible = two_sided_raw_score(
        cfg=cfg,
        up_mid=0.50,
        down_mid=0.50,
        up_price=0.49,
        down_price=0.48,
        size=10,
    )
    assert eligible is True
    assert score > 0

    score_small, eligible_small = two_sided_raw_score(
        cfg=cfg,
        up_mid=0.50,
        down_mid=0.50,
        up_price=0.49,
        down_price=0.49,
        size=9,
    )
    assert eligible_small is False
    assert score_small == 0

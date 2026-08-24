from decimal import Decimal as D

from src.sports_poly_live_fill_conversion_hotfix_v2 import _near_touch_activity_sort_key


def _row(*, gap: str, maker: str, hedge: str) -> dict:
    return {
        "maker_gap_to_ask": D(gap),
        "reported_maker_tick": D("0.01"),
        "activity_maker_shares": D(maker),
        "activity_hedge_shares": D(hedge),
        "improves_bid": True,
        "hedge_depth": D("5"),
        "quote_locked_profit": D("0.03"),
    }


def test_near_touch_beats_far_high_volume_market() -> None:
    near_dead = _row(gap="0.01", maker="0", hedge="0")
    far_active = _row(gap="0.37", maker="1287", hedge="2722")
    assert _near_touch_activity_sort_key(near_dead) < _near_touch_activity_sort_key(far_active)


def test_activity_breaks_ties_inside_near_touch_bucket() -> None:
    near_active = _row(gap="0.01", maker="100", hedge="80")
    near_dead = _row(gap="0.01", maker="0", hedge="0")
    assert _near_touch_activity_sort_key(near_active) < _near_touch_activity_sort_key(near_dead)

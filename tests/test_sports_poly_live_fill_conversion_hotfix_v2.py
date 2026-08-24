from decimal import Decimal as D

import src.sports_poly_live_fill_conversion_hotfix as base
from src.sports_poly_live_fill_conversion_hotfix_v2 import (
    STICKY_LIFECYCLE_SECONDS,
    _cleanup_clear,
    _near_touch_activity_sort_key,
    configure,
)


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


def test_closer_far_quote_beats_more_active_far_quote() -> None:
    closer_dead = _row(gap="0.02", maker="0", hedge="0")
    far_active = _row(gap="0.18", maker="10000", hedge="10000")
    assert _near_touch_activity_sort_key(closer_dead) < _near_touch_activity_sort_key(far_active)


def test_activity_breaks_ties_at_same_price_distance() -> None:
    active = _row(gap="0.01", maker="100", hedge="80")
    dead = _row(gap="0.01", maker="0", hedge="0")
    assert _near_touch_activity_sort_key(active) < _near_touch_activity_sort_key(dead)


def test_configure_makes_timer_non_operational_for_normal_mlb_lifecycle() -> None:
    prior = base.mlb_v3.STICKY_REST_SECONDS
    try:
        configure()
        assert base.mlb_v3.STICKY_REST_SECONDS == STICKY_LIFECYCLE_SECONDS == 21600.0
        assert base.mlb_v3.TARGET == D("0.03")
    finally:
        base.mlb_v3.STICKY_REST_SECONDS = prior


def test_pre_recovery_account_cleanup_fails_closed_on_any_ambiguity() -> None:
    assert _cleanup_clear({"unresolved": 0, "partial_unresolved": 0}) is True
    assert _cleanup_clear({"unresolved": 1, "partial_unresolved": 0}) is False
    assert _cleanup_clear({"unresolved": 0, "partial_unresolved": 1}) is False

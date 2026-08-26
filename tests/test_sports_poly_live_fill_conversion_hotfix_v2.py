from decimal import Decimal as D

import src.sports_poly_live_fill_conversion_hotfix as base
from src.sports_poly_live_fill_conversion_hotfix_v2 import (
    MAX_FILLABLE_GAP_TICKS,
    STICKY_LIFECYCLE_SECONDS,
    TAKER_TAKER_TARGET,
    _cleanup_clear,
    _fillable_gap,
    _near_touch_activity_sort_key,
    _taker_taker_rows,
    configure,
)


def _row(*, gap: str, maker: str, hedge: str, tick: str = "0.01") -> dict:
    return {
        "maker_gap_to_ask": D(gap),
        "reported_maker_tick": D(tick),
        "activity_maker_shares": D(maker),
        "activity_hedge_shares": D(hedge),
        "improves_bid": True,
        "hedge_depth": D("5"),
        "quote_locked_profit": D("0.03"),
    }


def _direction(
    direction: str,
    *,
    low: str = "low",
    high: str = "high",
    maker_ask: str,
    hedge_ask: str,
    hedge_depth: str = "5",
) -> dict:
    return {
        "direction": direction,
        "low_slug": low,
        "high_slug": high,
        "maker_ask": D(maker_ask),
        "hedge_ask": D(hedge_ask),
        "hedge_depth": D(hedge_depth),
        "family": "baseball_player_hits",
        "player": "Test Player",
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


def test_fillability_gate_allows_only_two_ticks_or_better() -> None:
    assert MAX_FILLABLE_GAP_TICKS == D("2")
    assert _fillable_gap(_row(gap="0.01", maker="0", hedge="0")) is True
    assert _fillable_gap(_row(gap="0.02", maker="0", hedge="0")) is True
    assert _fillable_gap(_row(gap="0.03", maker="10000", hedge="10000")) is False
    assert _fillable_gap(_row(gap="0.17", maker="10000", hedge="10000")) is False


def test_fillability_gate_respects_reported_tick() -> None:
    assert _fillable_gap(_row(gap="0.04", maker="0", hedge="0", tick="0.02")) is True
    assert _fillable_gap(_row(gap="0.05", maker="0", hedge="0", tick="0.02")) is False


def test_taker_taker_census_uses_both_asks_depths_and_exact_fees() -> None:
    rows = [
        _direction(
            "MAKER_LOW_HEDGE_HIGH",
            maker_ask="0.40",
            hedge_ask="0.50",
            hedge_depth="7",
        ),
        _direction(
            "MAKER_HIGH_HEDGE_LOW",
            maker_ask="0.50",
            hedge_ask="0.40",
            hedge_depth="3",
        ),
    ]
    candidates = _taker_taker_rows(rows)
    assert TAKER_TAKER_TARGET == D("0.03")
    assert len(candidates) == 1
    row = candidates[0]
    assert row["low_yes_ask"] == D("0.40")
    assert row["low_yes_depth"] == D("3")
    assert row["high_no_ask"] == D("0.50")
    assert row["high_no_depth"] == D("7")
    assert row["low_taker_fee"] == D("0.01")
    assert row["high_taker_fee"] == D("0.01")
    assert row["all_in_cost"] == D("0.92")
    assert row["locked_profit"] == D("0.08")


def test_taker_taker_census_rejects_sub_3c_and_missing_q1_depth() -> None:
    sub_floor = [
        _direction("MAKER_LOW_HEDGE_HIGH", maker_ask="0.48", hedge_ask="0.48"),
        _direction("MAKER_HIGH_HEDGE_LOW", maker_ask="0.48", hedge_ask="0.48"),
    ]
    assert _taker_taker_rows(sub_floor) == []

    no_low_depth = [
        _direction("MAKER_LOW_HEDGE_HIGH", maker_ask="0.40", hedge_ask="0.50", hedge_depth="5"),
        _direction("MAKER_HIGH_HEDGE_LOW", maker_ask="0.50", hedge_ask="0.40", hedge_depth="0.99"),
    ]
    assert _taker_taker_rows(no_low_depth) == []


def test_taker_taker_census_fails_closed_on_direction_snapshot_mismatch() -> None:
    rows = [
        _direction("MAKER_LOW_HEDGE_HIGH", maker_ask="0.40", hedge_ask="0.50"),
        _direction("MAKER_HIGH_HEDGE_LOW", maker_ask="0.51", hedge_ask="0.40"),
    ]
    assert _taker_taker_rows(rows) == []


def test_configure_caps_live_maker_lifecycle_at_three_minutes() -> None:
    prior = base.mlb_v3.STICKY_REST_SECONDS
    prior_ranked = base.mlb_v3._fresh_ranked
    try:
        configure()
        assert base.mlb_v3.STICKY_REST_SECONDS == STICKY_LIFECYCLE_SECONDS == 180.0
        assert base.mlb_v3.TARGET == D("0.03")
    finally:
        base.mlb_v3.STICKY_REST_SECONDS = prior
        base.mlb_v3._fresh_ranked = prior_ranked


def test_pre_recovery_account_cleanup_fails_closed_on_any_ambiguity() -> None:
    assert _cleanup_clear({"unresolved": 0, "partial_unresolved": 0}) is True
    assert _cleanup_clear({"unresolved": 1, "partial_unresolved": 0}) is False
    assert _cleanup_clear({"unresolved": 0, "partial_unresolved": 1}) is False

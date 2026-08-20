from decimal import Decimal

from src.gemini_kalshi_micro_live_preprobe_attribution import _checkpoint_reason

D = Decimal


def base_kwargs():
    return {
        "entry10": True,
        "entry_skew_ms": 10,
        "same_window": True,
        "same_episode": True,
        "whole10": True,
        "gate10_now": True,
        "post_activation_books": True,
        "gemini_age_ms": 20,
        "kalshi_age_ms": 30,
        "current_skew_ms": 10,
        "checks": {
            "gemini_price_ok": True,
            "gemini_depth_ok": True,
            "kalshi_price_ok": True,
            "kalshi_depth_ok": True,
            "both_marketable": True,
        },
        "quantity": D("1"),
        "locked_profit": D("0.03"),
    }


def reason(**changes):
    kwargs = base_kwargs()
    kwargs.update(changes)
    return _checkpoint_reason(**kwargs)


def test_ready_at_exact_floor_and_shape():
    assert reason() == "ready_50ms"


def test_boundary_failures_are_explicit():
    assert reason(entry10=False) == "entry_double_boundary_lt_10bp_or_missing"
    assert reason(whole10=False) == "double_boundary_not_held_through_episode"
    assert reason(gate10_now=False) == "current_double_boundary_lt_10bp_or_missing"


def test_entry_and_current_skew_fail_closed_at_over_50ms():
    assert reason(entry_skew_ms=51) == "entry_quote_skew_gt_50ms"
    assert reason(current_skew_ms=51) == "current_quote_skew_gt_50ms"
    assert reason(entry_skew_ms=50, current_skew_ms=50) == "ready_50ms"


def test_stale_or_pre_activation_books_are_rejected():
    assert reason(post_activation_books=False) == "book_timestamp_before_activation"
    assert reason(gemini_age_ms=251) == "book_stale_gt_250ms"
    assert reason(kalshi_age_ms=251) == "book_stale_gt_250ms"


def test_episode_identity_is_required():
    assert reason(same_window=False) == "market_window_changed"
    assert reason(same_episode=False) == "original_episode_closed_or_reopened"


def test_venue_price_and_depth_reasons_are_separate():
    checks = base_kwargs()["checks"].copy()
    checks["gemini_price_ok"] = False
    assert reason(checks=checks) == "gemini_original_limit_no_longer_marketable"

    checks = base_kwargs()["checks"].copy()
    checks["gemini_depth_ok"] = False
    assert reason(checks=checks) == "gemini_q1_depth_missing"

    checks = base_kwargs()["checks"].copy()
    checks["kalshi_price_ok"] = False
    assert reason(checks=checks) == "kalshi_original_limit_no_longer_marketable"

    checks = base_kwargs()["checks"].copy()
    checks["kalshi_depth_ok"] = False
    assert reason(checks=checks) == "kalshi_q1_depth_missing"


def test_quantity_and_profit_never_sneak_through():
    assert reason(quantity=D("2")) == "quantity_not_q1"
    assert reason(locked_profit=D("0.0299")) == "profit_below_3c"

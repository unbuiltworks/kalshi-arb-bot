from decimal import Decimal as D

import src.sports_poly_direct_mlb_campaign_q1_v3 as mlb_v3
import src.sports_poly_structural_live_supervisor as supervisor
from src.sports_poly_live_fill_conversion_hotfix import (
    SAFE_NO_MUTATION_STATUSES,
    STICKY_REST_SECONDS,
    _activity_sort_key,
    configure,
)


def test_safe_no_mutation_skips_do_not_hard_halt() -> None:
    original = set(supervisor.SAFE_CONTINUE_STATUSES)
    try:
        configure()
        assert SAFE_NO_MUTATION_STATUSES <= supervisor.SAFE_CONTINUE_STATUSES
        assert supervisor._hard_halt_reason(["SKIP_MLB_NEAR_TOUCH_NOT_PERSISTENT"]) is None
        assert supervisor._hard_halt_reason(["SKIP_MLB_CERT_LOST"]) is None
        assert supervisor._hard_halt_reason(["MAKER_PARTIAL_FLATTENED"]) == "MAKER_PARTIAL_FLATTENED"
    finally:
        supervisor.SAFE_CONTINUE_STATUSES.clear()
        supervisor.SAFE_CONTINUE_STATUSES.update(original)


def test_sticky_window_is_extended_without_changing_floor() -> None:
    prior = mlb_v3.STICKY_REST_SECONDS
    try:
        configure()
        assert mlb_v3.STICKY_REST_SECONDS == STICKY_REST_SECONDS == 180.0
        assert mlb_v3.TARGET == D("0.03")
    finally:
        mlb_v3.STICKY_REST_SECONDS = prior


def test_activity_ranking_prefers_flow_before_equal_queue_geometry() -> None:
    active = {
        "activity_maker_shares": D("20"),
        "activity_hedge_shares": D("15"),
        "improves_bid": True,
        "maker_gap_to_ask": D("0.01"),
        "hedge_depth": D("2"),
        "quote_locked_profit": D("0.03"),
    }
    dead = {
        "activity_maker_shares": D("0"),
        "activity_hedge_shares": D("0"),
        "improves_bid": True,
        "maker_gap_to_ask": D("0.01"),
        "hedge_depth": D("10"),
        "quote_locked_profit": D("0.03"),
    }
    assert _activity_sort_key(active) < _activity_sort_key(dead)

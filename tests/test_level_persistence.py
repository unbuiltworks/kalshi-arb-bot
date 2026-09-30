"""Persistence stats must refuse a cached feed, and must count honestly."""
from decimal import Decimal as D

import pytest

from src.l5_level_persistence import (
    FeedTooCoarse, analyse, feed_resolution, own_touch)


def _row(t, bid, ask, bid_levels=(), ask_levels=()):
    bids = [{"px": {"value": str(bid)}, "qty": "100"}]
    bids += [{"px": {"value": str(p)}, "qty": "10"} for p in bid_levels]
    offers = [{"px": {"value": str(ask)}, "qty": "100"}]
    offers += [{"px": {"value": str(p)}, "qty": "10"} for p in ask_levels]
    return {"recv_t": t, "bids": bids, "offers": offers}


def test_own_touch_inverts_the_offer_side():
    r = _row(0.0, "0.40", "0.60")
    assert own_touch(r, "up") == D("0.40")
    assert own_touch(r, "down") == D(1) - D("0.60")


def test_a_cached_feed_is_refused():
    """1,766 polls of 31 distinct states must not yield dwell statistics."""
    rows = [_row(i * 0.5, "0.40", "0.60") for i in range(1766)]
    res = feed_resolution(rows)
    assert res["distinct_states"] == 1
    assert res["sufficient"] is False
    with pytest.raises(FeedTooCoarse, match="cache TTL"):
        analyse(rows)


def test_polling_faster_than_the_cache_does_not_inflate_resolution():
    """Distinct states, not rows, is what counts."""
    slow = [_row(i * 1.0, "0.40", "0.60") for i in range(60)]
    fast = [_row(i * 0.1, "0.40", "0.60") for i in range(600)]
    assert (feed_resolution(slow)["distinct_states"]
            == feed_resolution(fast)["distinct_states"] == 1)


def test_touch_changes_are_counted_as_control_requotes():
    rows = []
    for i, bid in enumerate(["0.40", "0.41", "0.42", "0.43"]):
        for k in range(10):
            rows.append(_row(i * 10 + k, bid, "0.60"))
    a = analyse(rows, side="up", require_resolution=False)
    assert a["control_requotes"] == 3


def test_a_touch_that_comes_back_is_counted_as_an_oscillation():
    """The control abandons a level, then has to rejoin it at the back."""
    rows = ([_row(t, "0.40", "0.60") for t in range(0, 10)]
            + [_row(t, "0.41", "0.60") for t in range(10, 20)]
            + [_row(t, "0.40", "0.60") for t in range(20, 30)])
    a = analyse(rows, side="up", require_resolution=False)
    assert a["control_requotes"] == 2
    assert a["touch_returns"] == 1
    assert a["touch_return_share"] == 0.5


def test_a_return_after_the_horizon_is_not_an_oscillation():
    rows = ([_row(t, "0.40", "0.60") for t in range(0, 5)]
            + [_row(t, "0.41", "0.60") for t in range(5, 400)]
            + [_row(t, "0.40", "0.60") for t in range(400, 405)])
    a = analyse(rows, side="up", require_resolution=False)
    assert a["touch_returns"] == 0


def test_cache_limited_output_is_labelled_as_such():
    rows = [_row(i * 0.5, "0.40", "0.60") for i in range(100)]
    a = analyse(rows, side="up", require_resolution=False)
    assert a["fidelity"] == "CACHE_LIMITED__NOT_MARKET_BEHAVIOUR"

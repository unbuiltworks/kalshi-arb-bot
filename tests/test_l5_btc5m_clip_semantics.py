"""What a "0.10 clip" means, stated rather than inferred.

The owner's manual orders were 0.10 SHARES (about $0.06-$0.09 each). The
pilot brief says about $0.10 per order. Those are different sizes. The
controller sizes by dollars (`clip_usd`) unless a share count
(`clip_shares`) is given; both are rounded DOWN to the venue's 0.01
increment, both count in the exposure cap in dollars, and a clip below the
venue minimum is refused rather than rounded up.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C

FLAT = {"UP": {"qty": "0", "cost": "0"}, "DOWN": {"qty": "0", "cost": "0"}}


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv(C.CONTROLLER_ENV, "ON")


def decide(bid, ask, p_up, inventory=FLAT, resting=(), **params):
    return C.decide(book={"bid": bid, "ask": ask, "age_s": 0.0},
                    t_in_window_s=60.0, inventory=inventory,
                    resting=list(resting), params=C.Params(**params),
                    p_up=D(p_up), p_source="TEST")


def placed(r):
    return [o for o in r["orders"] if o["op"] == "PLACE"]


@pytest.mark.parametrize("bid,ask,want_px,want_qty", [
    ("0.60", "0.61", "0.58", "0.17"),      # 0.10 / 0.58 = 0.1724 -> 0.17
    ("0.75", "0.76", "0.73", "0.13"),      # 0.10 / 0.73 = 0.1369 -> 0.13
    ("0.90", "0.91", "0.88", "0.11"),      # 0.10 / 0.88 = 0.1136 -> 0.11
])
def test_by_default_a_clip_is_ten_cents_of_notional(bid, ask, want_px,
                                                    want_qty):
    r = decide(bid, ask, "0.97")
    (o,) = placed(r)
    assert r["action"] == C.OPEN_FAVORITE
    assert (D(o["price"]), D(o["qty"])) == (D(want_px), D(want_qty))
    assert D(o["price"]) * D(o["qty"]) <= D("0.10")      # never more


@pytest.mark.parametrize("bid,ask,want_px", [
    ("0.60", "0.61", "0.58"), ("0.75", "0.76", "0.73"),
    ("0.90", "0.91", "0.88")])
def test_a_share_clip_is_that_many_shares_at_any_price(bid, ask, want_px):
    r = decide(bid, ask, "0.97", clip_shares=D("0.10"))
    (o,) = placed(r)
    assert (D(o["price"]), D(o["qty"])) == (D(want_px), D("0.10"))
    # the owner's manual orders: about six to nine cents each
    assert D("0.05") <= D(o["price"]) * D(o["qty"]) <= D("0.09")


def test_the_two_meanings_are_different_sizes():
    a = placed(decide("0.60", "0.61", "0.97"))[0]
    b = placed(decide("0.60", "0.61", "0.97", clip_shares=D("0.10")))[0]
    assert D(a["qty"]) == D("0.17") and D(b["qty"]) == D("0.10")


@pytest.mark.parametrize("shares,want", [
    ("0.109", "0.10"), ("0.019", "0.01"), ("0.25", "0.25")])
def test_a_share_clip_is_rounded_down_to_the_increment(shares, want):
    (o,) = placed(decide("0.60", "0.61", "0.97", clip_shares=D(shares)))
    assert D(o["qty"]) == D(want)


@pytest.mark.parametrize("params", [
    {"clip_shares": D("0.009")}, {"clip_shares": D("0")},
    {"clip_shares": D("-1")}, {"clip_usd": D("0.005")}])
def test_a_clip_below_the_venue_minimum_is_refused_not_rounded_up(params):
    r = decide("0.60", "0.61", "0.97", **params)
    assert placed(r) == []
    assert r["action"] == C.HOLD_OR_SKIP


def test_either_clip_counts_in_the_exposure_cap_in_dollars():
    resting = [{"id": "x", "side": "DOWN", "price": "0.30", "qty": "11.6",
                "age_s": 1.0}]                     # $3.48 already resting
    for params in ({}, {"clip_shares": D("0.10")}):
        r = decide("0.60", "0.61", "0.97", resting=resting, **params)
        assert all(o["side"] != "UP" for o in placed(r)), params
        exp = r["exposure"]
        assert D(str(exp["total_exposure_usd"])) >= D("3.48")


def test_replacing_an_order_does_not_count_both_commitments_as_room():
    """A resting favorite order that is about to be re-priced still counts
    until it is gone: the replacement may not be sized as if the old one
    had already been cancelled into free room."""
    resting = [{"id": "old", "side": "UP", "price": "0.50", "qty": "6.9",
                "age_s": 999.0}]                   # $3.45, stale
    r = decide("0.60", "0.61", "0.97", resting=resting)
    cancels = [o for o in r["orders"] if o["op"] == "CANCEL"]
    assert [o["id"] for o in cancels] == ["old"]
    spend = sum(D(o["price"]) * D(o["qty"]) for o in placed(r))
    assert D("3.45") + spend <= D("5.00") - D("1.50") + D("0.0001") \
        or placed(r) == []

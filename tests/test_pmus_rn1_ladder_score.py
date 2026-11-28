"""Full-ladder and marginal reward scoring (dev/chatgpt-rn1-ladder-score)
and the hold-through-a-dip rule (RN1_NET_HOLD_S)."""
import time
from decimal import Decimal as D

import pytest

from src import pmus_sports_rewards as RW
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_continuous_controller import (  # noqa: F401
    SLUG, eng, resting)
from tests.test_pmus_rn1_ladder import live  # noqa: F401

BIDS = [(0.50, 10.0), (0.49, 20.0), (0.48, 40.0)]
PER = {"df": 0.5, "targetSize": 50.0, "pool_rate_usd_s": 0.01,
       "maxSpread": None}


def test_single_level_ladder_equals_side_score():
    a = RW.side_score(BIDS, 0.49, 5.0, 0.01, 0.5, 50.0, True, 0.0)
    b = RW.ladder_side_score(BIDS, [(0.49, 5.0)], 0.01, 0.5, 50.0, True, [])
    assert a["share"] == pytest.approx(b["share"])
    assert a["our_score"] == pytest.approx(b["our_score"])


def test_two_owned_levels_each_count_once():
    """We rest 5 at 0.50 and 5 at 0.49, both already in the public book.
    Score: 5*1 + 5*0.5 = 7.5 of total 10*1 + 20*0.5 + 40*0.25 = 30 (the
    level that reaches the target is walked whole, as the venue rule says)
    -> 0.25. Counting ours twice would give more."""
    own = [(0.50, 5.0), (0.49, 5.0)]
    s = RW.ladder_side_score(BIDS, own, 0.01, 0.5, 50.0, True, own)
    assert s["our_score"] == pytest.approx(7.5)
    assert s["share"] == pytest.approx(7.5 / 30.0)


def test_marginal_reward_is_after_minus_before():
    book = {"bids": [{"px": {"value": str(p)}, "qty": str(q)}
                     for p, q in BIDS],
            "offers": [{"px": {"value": "0.52"}, "qty": "60"}]}
    m = RW.marginal_ladder_reward(book, PER, [(0.50, 2.0)], [], 0.01,
                                  [], [], "YES", 0.48, 2.0)
    assert m["incremental_reward_usd_per_hour"] == pytest.approx(
        (m["after"]["rate_usd_s"] - m["before"]["rate_usd_s"]) * 3600)
    assert m["incremental_reward_usd_per_hour"] >= 0
    with pytest.raises(ValueError):
        RW.marginal_ladder_reward(book, PER, [], [], 0.01, [], [],
                                  "YES", 1.2, 1.0)


# ------------------------------------------------ hold through a dip
def _neg(e, monkeypatch):
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: False)


def test_a_dip_keeps_resting_bids_and_places_nothing_new(live, monkeypatch):
    e, v = live
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(M, "RN1_NET_HOLD_S", 300.0)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: True)
    e.step_market(SLUG)
    before = sorted(resting(v))
    assert before
    n_orders = len(v.orders)
    _neg(e, monkeypatch)
    e.step_market(SLUG)
    assert sorted(resting(v)) == before          # kept: queue priority
    assert len(v.orders) == n_orders             # nothing new placed


def test_after_the_hold_the_bids_are_cancelled(live, monkeypatch):
    e, v = live
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(M, "RN1_NET_HOLD_S", 300.0)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: True)
    e.step_market(SLUG)
    _neg(e, monkeypatch)
    e.step_market(SLUG)
    assert resting(v)
    e.b.m(SLUG)["net_neg_since"] = time.time() - 301
    e.step_market(SLUG)
    assert resting(v) == []


def test_without_hold_a_dip_cancels_at_once(live, monkeypatch):
    e, v = live
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: True)
    e.step_market(SLUG)
    _neg(e, monkeypatch)
    e.step_market(SLUG)
    assert resting(v) == []


def test_a_held_bid_that_would_set_the_price_is_cancelled(live, monkeypatch):
    """The best bid falls below our resting YES bid: keeping it would make
    us the price-setting bid, so it goes; the NO side stays."""
    e, v = live
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(M, "RN1_NET_HOLD_S", 300.0)
    monkeypatch.setattr(M, "RN1_JOIN_TOUCH", False)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: True)
    e.step_market(SLUG)
    yes_px = max(D(p) for s_, p, _q in resting(v) if s_ == "YES")
    _neg(e, monkeypatch)
    v.bid = yes_px - D("0.01")                    # touch now below our bid
    e.step_market(SLUG)
    assert all(D(p) <= v.bid for s_, p, _q in resting(v) if s_ == "YES")
    assert any(s_ == "NO" for s_, _p, _q in resting(v))


def test_any_other_block_still_cancels_during_a_hold(live, monkeypatch):
    e, v = live
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(M, "RN1_NET_HOLD_S", 300.0)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: True)
    e.step_market(SLUG)
    monkeypatch.setattr(M, "VOL_PAUSE_USD", D("0.001"))
    e.b.m(SLUG)["vol_pause_until"] = time.time() + 600
    e.step_market(SLUG)
    assert resting(v) == []

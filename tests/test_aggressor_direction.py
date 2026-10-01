"""DEFECT C: a resting queue may be consumed only by correctly directed flow.

The frozen model consumed `leg.ahead` from ANY print at or through our price.
Measured on the authenticated 1915z tape, at or through 0.3500 that counted
126,301.99 of volume where only 40,795.37 was SELL-aggressor -- a 67.7%
over-count of bid-queue consumption, in every arm and every batch.

The direction rule was established EMPIRICALLY from that tape, not assumed:
`taker.side` alone decides which side of the book a print consumed, and
`outcomeSide` does not flip it (SELL lands AT_BID 86-87%, BUY lands AT_ASK
84-90%). Our "up" leg rests on the YES bid, so only SELL can reach it; our
"down" leg rests on the YES ask, so only BUY can.
"""
from decimal import Decimal as D

import pytest

from src.l5_two_sided import ARM_REPLICATION, TwoSided


def eng():
    c = TwoSided(arm=ARM_REPLICATION, clip=D(1))
    c.window_end = 10_000.0
    return c


def feed(c, t, bid="0.40", ask="0.60"):
    c.on_book({"bid": bid, "ask": ask, "bid_qty": "50", "ask_qty": "50",
               "status": "OK",
               "_ladder": {"bids": {D(bid): D(50)},
                           "offers": {D(ask): D(50)}}}, t)


def test_a_sell_aggressor_consumes_our_resting_up_queue():
    c = eng()
    feed(c, 1000.0)
    leg = c.legs["up"]
    leg.ahead = D(0)
    c.on_trade(leg.px, leg.remaining(), 1001.0, "ORDER_SIDE_SELL")
    assert c.qty["up"] == D(1), "a SELL aggressor must reach our bid"


def test_a_buy_aggressor_cannot_consume_our_resting_up_queue():
    """THE DEFECT. A BUY aggressor lifts offers; it can never fill our bid."""
    c = eng()
    feed(c, 1000.0)
    leg = c.legs["up"]
    leg.ahead = D(0)
    c.on_trade(leg.px, leg.remaining(), 1001.0, "ORDER_SIDE_BUY")
    assert c.qty["up"] == D(0), "a BUY aggressor must NOT fill our resting bid"
    assert c.legs["up"] is leg and leg.filled == 0


def test_a_buy_aggressor_consumes_our_resting_down_queue():
    c = eng()
    feed(c, 1000.0)
    leg = c.legs["down"]
    leg.ahead = D(0)
    # a down leg at own price p sits at the YES ask level 1 - p
    c.on_trade(D(1) - leg.px, leg.remaining(), 1001.0, "ORDER_SIDE_BUY")
    assert c.qty["down"] == D(1)


def test_a_sell_aggressor_cannot_consume_our_resting_down_queue():
    c = eng()
    feed(c, 1000.0)
    leg = c.legs["down"]
    leg.ahead = D(0)
    c.on_trade(D(1) - leg.px, leg.remaining(), 1001.0, "ORDER_SIDE_SELL")
    assert c.qty["down"] == D(0)


def test_wrongly_directed_flow_does_not_even_consume_queue_ahead():
    """Over-counting the QUEUE is the subtler half of the defect: it makes the
    next correctly-directed print fill us far too early."""
    c = eng()
    feed(c, 1000.0)
    leg = c.legs["up"]
    ahead0 = leg.ahead
    assert ahead0 > 0
    c.on_trade(leg.px, D(40), 1001.0, "ORDER_SIDE_BUY")
    assert leg.ahead == ahead0, "wrong-direction flow must not eat our queue"
    c.on_trade(leg.px, D(40), 1002.0, "ORDER_SIDE_SELL")
    assert leg.ahead < ahead0, "correctly directed flow must eat our queue"


def test_an_unknown_aggressor_fills_nothing_and_is_counted():
    """Conservative by construction: a print we cannot direct must not invent a
    fill, and its share must stay visible."""
    c = eng()
    feed(c, 1000.0)
    leg = c.legs["up"]
    leg.ahead = D(0)
    c.on_trade(leg.px, leg.remaining(), 1001.0, None)
    assert c.qty["up"] == D(0)
    assert c.report(winner="up")["unresolved_prints"] == 1


@pytest.mark.parametrize("raw,expect_up_fill", [
    ("ORDER_SIDE_SELL", True), ("SELL", True), ("sell", True),
    ("ORDER_SIDE_BUY", False), ("BUY", False), ("buy", False)])
def test_aggressor_label_forms_are_normalised(raw, expect_up_fill):
    c = eng()
    feed(c, 1000.0)
    leg = c.legs["up"]
    leg.ahead = D(0)
    c.on_trade(leg.px, leg.remaining(), 1001.0, raw)
    assert (c.qty["up"] == D(1)) is expect_up_fill

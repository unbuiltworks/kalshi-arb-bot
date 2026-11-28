"""THE DEPTH MAPPING, PINNED IN BOTH DIRECTIONS, BEFORE ANYTHING RELIES ON IT.

A previous verbal statement of this rule was BACKWARDS. A backwards mapping
does not fail loudly: it reads a real number off the wrong side of the book,
so every downstream executability verdict is confidently wrong. That is the
worst failure shape available to an instrumentation change, which is why this
file is written first and stands on its own.

THE INVARIANT IS A PROPERTY OF THE ORDER'S SIDE, not of the inventory:

    an order that BUYS UP   executes against ask_qty
    an order that BUYS DOWN executes against bid_qty

COMPLETE buys the DEFICIT side, so the inventory statement follows from the
order statement rather than replacing it:

    holding DOWN -> COMPLETE buys UP   -> ask_qty
    holding UP   -> COMPLETE buys DOWN -> bid_qty

DERIVATION (why buying DOWN reads the BID side). Selling one NO at q pays
YES:+q, NO:q-1. Buying one YES at 1-q pays YES:q, NO:q-1. The two are the
same instrument, so the best NO ask IS 1 - best YES bid, and the quantity
available there is the quantity resting AT THE YES BID. The recorder quotes
YES; `bid_qty` is therefore the executable depth for a DOWN buy.

The price and the depth must come off the SAME side of the book -- an ask
price paired with bid depth is the backwards mapping wearing a disguise --
so that pairing is pinned here too.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import pmus_cross_shadow_live as xs


BOOK = {"slug": "aec-mlb-lad-mia-2026-09-13",
        "bid": "0.4950", "ask": "0.5900",
        "bid_qty": "5.0000", "ask_qty": "61.0000",
        "both_sides_served": True}


# ------------------------------------------------- 8/9: the order's side
def test_8_an_order_that_buys_UP_maps_to_ask_qty():
    assert xs.executable_depth_field(buys_up=True) == "ask_qty"
    depth, status = xs.executable_depth(BOOK, buys_up=True, quote_age_s=1.0)
    assert status == xs.DEPTH_OBSERVED
    assert depth == D("61.0000")


def test_9_an_order_that_buys_DOWN_maps_to_bid_qty():
    assert xs.executable_depth_field(buys_up=False) == "bid_qty"
    depth, status = xs.executable_depth(BOOK, buys_up=False, quote_age_s=1.0)
    assert status == xs.DEPTH_OBSERVED
    assert depth == D("5.0000")


def test_the_two_directions_are_not_the_same_number():
    """Asymmetric fixture on purpose: a mapping that silently returned the
    same field for both sides would pass a symmetric one."""
    up, _ = xs.executable_depth(BOOK, buys_up=True, quote_age_s=1.0)
    dn, _ = xs.executable_depth(BOOK, buys_up=False, quote_age_s=1.0)
    assert up != dn
    assert (up, dn) == (D("61.0000"), D("5.0000"))


def test_the_backwards_mapping_is_named_and_refused():
    """The exact error that was made before, asserted as an error."""
    assert xs.executable_depth_field(buys_up=True) != "bid_qty"
    assert xs.executable_depth_field(buys_up=False) != "ask_qty"


# ------------------------------------ 10/11: COMPLETE buys the deficit side
def test_10_holding_DOWN_completes_by_buying_UP_and_reads_ask_qty():
    assert xs.complete_buys_up(held_residual=D("-0.02")) is True
    buys_up = xs.complete_buys_up(held_residual=D("-0.02"))
    assert xs.executable_depth_field(buys_up=buys_up) == "ask_qty"
    depth, status = xs.executable_depth(BOOK, buys_up=buys_up, quote_age_s=1.0)
    assert (depth, status) == (D("61.0000"), xs.DEPTH_OBSERVED)


def test_11_holding_UP_completes_by_buying_DOWN_and_reads_bid_qty():
    assert xs.complete_buys_up(held_residual=D("0.02")) is False
    buys_up = xs.complete_buys_up(held_residual=D("0.02"))
    assert xs.executable_depth_field(buys_up=buys_up) == "bid_qty"
    depth, status = xs.executable_depth(BOOK, buys_up=buys_up, quote_age_s=1.0)
    assert (depth, status) == (D("5.0000"), xs.DEPTH_OBSERVED)


def test_a_flat_residual_has_no_complement_to_buy():
    """Fail closed: `complete_buys_up` must not silently pick a side when
    there is no deficit -- the session treats that as a HOLD."""
    assert xs.complete_buys_up(held_residual=D("0")) is None


# --------------------------------- price and depth come off the SAME side
def test_the_complement_price_for_a_UP_buy_is_the_YES_ask():
    px, why = xs.executable_complement_price(BOOK, buys_up=True)
    assert px == D("0.5900") and why is None


def test_the_complement_price_for_a_DOWN_buy_is_one_minus_the_YES_bid():
    """Buying DOWN pays 1 - best YES bid; the depth is what rests AT that
    bid. Same side of the book for both, which is the whole invariant."""
    px, why = xs.executable_complement_price(BOOK, buys_up=False)
    assert px == D("1") - D("0.4950") == D("0.5050")
    assert why is None


def test_price_and_depth_are_always_read_from_the_same_side():
    """A one-sided book proves the pairing: strip ONE side and BOTH the
    price and the depth for that direction must go unknown together, while
    the other direction stays fully readable."""
    no_asks = {k: v for k, v in BOOK.items() if k not in ("ask", "ask_qty")}
    px, why = xs.executable_complement_price(no_asks, buys_up=True)
    depth, status = xs.executable_depth(no_asks, buys_up=True, quote_age_s=1.0)
    assert px is None and why is not None
    assert depth is None and status == xs.DEPTH_UNKNOWN_NOT_RECORDED
    # the DOWN direction reads the bid side and is unaffected
    px2, why2 = xs.executable_complement_price(no_asks, buys_up=False)
    depth2, status2 = xs.executable_depth(no_asks, buys_up=False,
                                          quote_age_s=1.0)
    assert px2 == D("0.5050") and why2 is None
    assert depth2 == D("5.0000") and status2 == xs.DEPTH_OBSERVED

    no_bids = {k: v for k, v in BOOK.items() if k not in ("bid", "bid_qty")}
    assert xs.executable_complement_price(no_bids, buys_up=False)[0] is None
    assert xs.executable_depth(no_bids, buys_up=False,
                               quote_age_s=1.0)[0] is None
    assert xs.executable_complement_price(no_bids, buys_up=True)[0] == \
        D("0.5900")
    assert xs.executable_depth(no_bids, buys_up=True,
                               quote_age_s=1.0)[0] == D("61.0000")


# ------------------------------ the mapping survives into the shadow row
def test_the_capture_records_which_field_it_used_in_both_directions():
    up = xs.observational_capture(
        row=BOOK, book_source="spring_watch",
        status_utc="2026-09-13T20:37:27+00:00",
        probe_receipt_utc="2026-09-13T20:37:27.500000+00:00",
        authenticated_age_s=0.4, buys_up=True)
    dn = xs.observational_capture(
        row=BOOK, book_source="spring_watch",
        status_utc="2026-09-13T20:37:27+00:00",
        probe_receipt_utc="2026-09-13T20:37:27.500000+00:00",
        authenticated_age_s=0.4, buys_up=False)
    assert up["order_buys"] == "UP"
    assert up["executable_depth_field"] == "ask_qty"
    assert up["executable_depth"] == "61.0000"
    assert dn["order_buys"] == "DOWN"
    assert dn["executable_depth_field"] == "bid_qty"
    assert dn["executable_depth"] == "5.0000"
    # both rows still carry BOTH raw quantities verbatim, so a reader can
    # recheck the mapping against the row rather than trusting this code
    for row in (up, dn):
        assert row["bid_qty"] == "5.0000" and row["ask_qty"] == "61.0000"

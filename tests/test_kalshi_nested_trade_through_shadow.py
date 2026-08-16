from decimal import Decimal

from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook
from src.kalshi_nested_trade_through_shadow import hedge_top, trade_throughs_shadow

D = Decimal


def test_yes_maker_requires_no_taker_and_print_at_or_below_bid():
    assert trade_throughs_shadow(
        maker_side="yes",
        maker_price=D("0.95"),
        trade_msg={"taker_outcome_side": "no", "yes_price_dollars": "0.95"},
    )
    assert trade_throughs_shadow(
        maker_side="yes",
        maker_price=D("0.95"),
        trade_msg={"taker_outcome_side": "no", "yes_price_dollars": "0.94"},
    )
    assert not trade_throughs_shadow(
        maker_side="yes",
        maker_price=D("0.95"),
        trade_msg={"taker_outcome_side": "yes", "yes_price_dollars": "0.94"},
    )
    assert not trade_throughs_shadow(
        maker_side="yes",
        maker_price=D("0.95"),
        trade_msg={"taker_outcome_side": "no", "yes_price_dollars": "0.96"},
    )


def test_no_maker_uses_no_price_and_yes_taker():
    assert trade_throughs_shadow(
        maker_side="no",
        maker_price=D("0.93"),
        trade_msg={"taker_outcome_side": "yes", "no_price_dollars": "0.93"},
    )
    assert not trade_throughs_shadow(
        maker_side="no",
        maker_price=D("0.93"),
        trade_msg={"taker_outcome_side": "no", "no_price_dollars": "0.90"},
    )


def test_legacy_taker_side_fallback():
    assert trade_throughs_shadow(
        maker_side="yes",
        maker_price=D("0.70"),
        trade_msg={"taker_side": "no", "yes_price_dollars": "0.70"},
    )


def test_hedge_top_reconstructs_yes_and_no_asks_from_opposite_bids():
    book = KalshiOrderBook("TEST")
    book.yes = {D("0.40"): D("12"), D("0.42"): D("7")}
    book.no = {D("0.55"): D("9"), D("0.57"): D("4")}

    yes_ask = hedge_top(book, "yes")
    no_ask = hedge_top(book, "no")

    assert yes_ask == (D("0.43"), D("4"))
    assert no_ask == (D("0.58"), D("7"))


def test_hedge_top_fails_closed_when_opposite_book_missing():
    book = KalshiOrderBook("TEST")
    book.yes = {D("0.50"): D("2")}
    assert hedge_top(book, "yes") is None
    assert hedge_top(book, "no") == (D("0.50"), D("2"))

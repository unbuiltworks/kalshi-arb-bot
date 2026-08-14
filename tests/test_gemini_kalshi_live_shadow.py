from decimal import Decimal

import pytest

from src.gemini_kalshi_live_shadow import (
    parse_gemini_book_ticker,
    parse_kalshi_orderbook,
)


def test_parse_gemini_book_ticker():
    tob = parse_gemini_book_ticker({
        "s": "GEMI-BTC15M2608141945-UP",
        "b": "0.48",
        "B": "50",
        "a": "0.52",
        "A": "32",
    }, received_ms=1234)
    assert tob.market_id == "GEMI-BTC15M2608141945-UP"
    assert tob.ts_ms == 1234
    assert tob.yes_bid == Decimal("0.48")
    assert tob.yes_ask == Decimal("0.52")
    assert tob.yes_bid_size == Decimal("50")
    assert tob.yes_ask_size == Decimal("32")


def test_parse_kalshi_orderbook_transforms_no_bid_to_yes_ask():
    tob = parse_kalshi_orderbook({
        "orderbook_fp": {
            "yes_dollars": [["0.44", "10"], ["0.46", "7"]],
            "no_dollars": [["0.50", "3"], ["0.52", "9"]],
        }
    }, ticker="KXBTC15M-TEST", received_ms=2222)
    assert tob.yes_bid == Decimal("0.46")
    assert tob.yes_bid_size == Decimal("7")
    assert tob.yes_ask == Decimal("0.48")
    assert tob.yes_ask_size == Decimal("9")
    assert tob.ts_ms == 2222


def test_parse_kalshi_requires_two_sided_book():
    with pytest.raises(ValueError):
        parse_kalshi_orderbook({
            "orderbook_fp": {"yes_dollars": [["0.46", "7"]], "no_dollars": []}
        }, ticker="KXBTC15M-TEST", received_ms=2222)

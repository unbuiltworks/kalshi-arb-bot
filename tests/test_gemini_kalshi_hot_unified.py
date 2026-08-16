from decimal import Decimal as D

from src.gemini_kalshi_multi_ws_hot_unified import (
    UnifiedYesPriceKalshiOrderBook,
    _unified_subscription,
)


def test_unified_subscription_is_explicit_and_forward_compatible():
    assert _unified_subscription("KXBTC15M-TEST") == {
        "id": 1,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta"],
            "market_ticker": "KXBTC15M-TEST",
            "use_yes_price": True,
        },
    }


def test_unified_no_levels_are_direct_yes_asks_and_lowest_wins():
    book = UnifiedYesPriceKalshiOrderBook("KXBTC15M-TEST")
    assert book.apply({
        "type": "orderbook_snapshot",
        "seq": 10,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "yes_dollars_fp": [["0.40", "5"]],
            "no_dollars_fp": [["0.60", "3"], ["0.55", "4"]],
        },
    })
    top = book.top(1000)
    assert top is not None
    assert top.yes_bid == D("0.40")
    assert top.yes_bid_size == D("5")
    assert top.yes_ask == D("0.55")
    assert top.yes_ask_size == D("4")


def test_unified_one_sided_book_preserves_only_executable_direction():
    book = UnifiedYesPriceKalshiOrderBook("KXBTC15M-TEST")
    assert book.apply({
        "type": "orderbook_snapshot",
        "seq": 1,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "yes_dollars_fp": [],
            "no_dollars_fp": [["0.46", "7"]],
        },
    })
    top = book.top(1000)
    assert top is not None
    assert top.yes_bid == D("0")
    assert top.yes_bid_size == D("0")
    assert top.yes_ask == D("0.46")
    assert top.yes_ask_size == D("7")


def test_unified_sequence_gap_still_fails_closed():
    book = UnifiedYesPriceKalshiOrderBook("KXBTC15M-TEST")
    assert book.apply({
        "type": "orderbook_snapshot",
        "seq": 20,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "yes_dollars_fp": [["0.40", "1"]],
            "no_dollars_fp": [["0.60", "1"]],
        },
    })
    try:
        book.apply({
            "type": "orderbook_delta",
            "seq": 22,
            "msg": {
                "market_ticker": "KXBTC15M-TEST",
                "side": "yes",
                "price_dollars": "0.41",
                "delta_fp": "1",
            },
        })
    except Exception as exc:
        assert exc.__class__.__name__ == "SequenceGap"
    else:
        raise AssertionError("sequence gap must fail closed")

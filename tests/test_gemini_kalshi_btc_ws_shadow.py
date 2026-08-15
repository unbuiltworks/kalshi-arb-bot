from decimal import Decimal as D

from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook, _subscription


def test_snapshot_and_delta_reconstruct_top_of_book():
    book = KalshiOrderBook("KXBTC15M-TEST")
    assert book.apply({
        "type": "orderbook_snapshot",
        "seq": 10,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "yes_dollars_fp": [["0.40", "5.00"], ["0.43", "7.00"]],
            "no_dollars_fp": [["0.54", "3.00"], ["0.55", "4.00"]],
        },
    })
    top = book.top(1000)
    assert top is not None
    assert top.yes_bid == D("0.43")
    assert top.yes_bid_size == D("7.00")
    assert top.yes_ask == D("0.45")
    assert top.yes_ask_size == D("4.00")

    assert book.apply({
        "type": "orderbook_delta",
        "seq": 11,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "price_dollars": "0.55",
            "delta_fp": "-4.00",
            "side": "no",
            "ts_ms": 900,
        },
    })
    top = book.top(1010)
    assert top is not None
    assert top.yes_ask == D("0.46")
    assert top.yes_ask_size == D("3.00")
    assert book.exchange_ts_ms == 900


def test_delta_can_add_new_best_level_and_remove_level():
    book = KalshiOrderBook("KXBTC15M-TEST")
    book.apply({
        "type": "orderbook_snapshot",
        "seq": 1,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "yes_dollars_fp": [["0.40", "5.00"]],
            "no_dollars_fp": [["0.55", "4.00"]],
        },
    })
    assert book.apply({
        "type": "orderbook_delta",
        "seq": 2,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "price_dollars": "0.44",
            "delta_fp": "2.50",
            "side": "yes",
            "ts_ms": 100,
        },
    })
    assert book.top(110).yes_bid == D("0.44")
    assert book.apply({
        "type": "orderbook_delta",
        "seq": 3,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "price_dollars": "0.44",
            "delta_fp": "-2.50",
            "side": "yes",
            "ts_ms": 105,
        },
    })
    assert book.top(115).yes_bid == D("0.40")


def test_stale_or_wrong_market_messages_are_ignored():
    book = KalshiOrderBook("KXBTC15M-TEST")
    assert not book.apply({
        "type": "orderbook_snapshot",
        "seq": 1,
        "msg": {"market_ticker": "OTHER", "yes_dollars_fp": [], "no_dollars_fp": []},
    })
    assert book.apply({
        "type": "orderbook_snapshot",
        "seq": 2,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "yes_dollars_fp": [["0.40", "1"]],
            "no_dollars_fp": [["0.55", "1"]],
        },
    })
    assert not book.apply({
        "type": "orderbook_delta",
        "seq": 2,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "price_dollars": "0.41",
            "delta_fp": "1",
            "side": "yes",
        },
    })


def test_subscription_is_market_data_only():
    sub = _subscription("KXBTC15M-TEST")
    assert sub == {
        "id": 1,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta"],
            "market_ticker": "KXBTC15M-TEST",
        },
    }

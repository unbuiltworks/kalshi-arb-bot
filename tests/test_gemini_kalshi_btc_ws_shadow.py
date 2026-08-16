from decimal import Decimal as D

from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook, _gemini_top, _subscription
from src.gemini_kalshi_execution_shadow import TopOfBook, evaluate_pair, zero_fee


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


def test_gemini_bid_only_preserves_executable_no_ask():
    top = _gemini_top(
        {"bids": [["0.9700", "515.00"]], "asks": []},
        "GEMI-BTC15M-TEST-UP",
        1000,
    )
    assert top is not None
    assert top.yes_bid == D("0.9700")
    assert top.yes_bid_size == D("515.00")
    assert top.yes_ask == D("1")
    assert top.yes_ask_size == D("0")
    assert top.no_ask == D("0.0300")
    assert top.no_ask_size == D("515.00")

    kalshi = TopOfBook(
        venue="kalshi",
        market_id="KXBTC15M-TEST",
        ts_ms=1000,
        yes_bid=D("0.01"),
        yes_bid_size=D("100"),
        yes_ask=D("0.02"),
        yes_ask_size=D("100"),
    )
    candidates = evaluate_pair(top, kalshi, gemini_fee=zero_fee, kalshi_fee=zero_fee)
    assert [c.direction for c in candidates] == ["KALSHI_YES_GEMINI_NO"]
    assert candidates[0].quantity == D("100")
    assert candidates[0].locked_profit == D("95.0000")


def test_gemini_ask_only_preserves_executable_yes_ask():
    top = _gemini_top(
        {"bids": [], "asks": [["0.0300", "25.00"]]},
        "GEMI-BTC15M-TEST-UP",
        1000,
    )
    assert top is not None
    assert top.yes_bid == D("0")
    assert top.yes_bid_size == D("0")
    assert top.yes_ask == D("0.0300")
    assert top.yes_ask_size == D("25.00")


def test_gemini_empty_book_is_not_a_quote():
    assert _gemini_top({"bids": [], "asks": []}, "GEMI-BTC15M-TEST-UP", 1000) is None


def test_kalshi_yes_bid_only_preserves_executable_no_ask():
    book = KalshiOrderBook("KXBTC15M-TEST")
    book.yes = {D("0.9700"): D("12")}
    top = book.top(1000)
    assert top is not None
    assert top.yes_bid == D("0.9700")
    assert top.yes_bid_size == D("12")
    assert top.yes_ask == D("1")
    assert top.yes_ask_size == D("0")
    assert top.no_ask == D("0.0300")
    assert top.no_ask_size == D("12")


def test_kalshi_no_bid_only_preserves_executable_yes_ask():
    book = KalshiOrderBook("KXBTC15M-TEST")
    book.no = {D("0.9600"): D("20")}
    top = book.top(1000)
    assert top is not None
    assert top.yes_bid == D("0")
    assert top.yes_bid_size == D("0")
    assert top.yes_ask == D("0.0400")
    assert top.yes_ask_size == D("20")


def test_kalshi_empty_book_is_not_a_quote():
    assert KalshiOrderBook("KXBTC15M-TEST").top(1000) is None

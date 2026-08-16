from decimal import Decimal as D

import pytest

from src.gemini_kalshi_execution_shadow import TopOfBook, zero_fee
from src.gemini_kalshi_multi_ws_hot import (
    GuardedKalshiOrderBook,
    SequenceGap,
    _kalshi_subscription,
    _small_q_candidates,
)


def _snapshot(seq: int = 10):
    return {
        "type": "orderbook_snapshot",
        "seq": seq,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "yes_dollars_fp": [["0.40", "5"]],
            "no_dollars_fp": [["0.55", "5"]],
        },
    }


def _delta(seq: int):
    return {
        "type": "orderbook_delta",
        "seq": seq,
        "msg": {
            "market_ticker": "KXBTC15M-TEST",
            "price_dollars": "0.41",
            "delta_fp": "1",
            "side": "yes",
            "ts_ms": 1000,
        },
    }


def test_sequence_gap_raises_instead_of_corrupting_book():
    book = GuardedKalshiOrderBook("KXBTC15M-TEST")
    assert book.apply(_snapshot(10))
    with pytest.raises(SequenceGap):
        book.apply(_delta(12))
    assert book.seq == 10
    assert D("0.41") not in book.yes


def test_duplicate_sequence_is_ignored():
    book = GuardedKalshiOrderBook("KXBTC15M-TEST")
    assert book.apply(_snapshot(10))
    assert not book.apply(_delta(10))
    assert book.seq == 10


def test_subscription_pins_legacy_two_scale_prices():
    sub = _kalshi_subscription("KXBTC15M-TEST")
    assert sub["params"]["channels"] == ["orderbook_delta"]
    assert sub["params"]["market_ticker"] == "KXBTC15M-TEST"
    assert sub["params"]["use_yes_price"] is False


def test_small_q_gate_requires_full_integer_depth_and_capital_limit():
    g = TopOfBook(
        venue="gemini",
        market_id="G",
        ts_ms=1000,
        yes_bid=D("0.60"),
        yes_bid_size=D("5"),
        yes_ask=D("0.40"),
        yes_ask_size=D("2.5"),
    )
    k = TopOfBook(
        venue="kalshi",
        market_id="K",
        ts_ms=1000,
        yes_bid=D("0.70"),
        yes_bid_size=D("5"),
        yes_ask=D("0.30"),
        yes_ask_size=D("5"),
    )
    rows = _small_q_candidates(
        g,
        k,
        kalshi_fee=zero_fee,
        max_quote_skew_ms=100,
        capital_limit=D("5"),
    )
    # Gemini YES depth is only 2.5, so that direction can qualify at q1/q2 but
    # never by silently treating 2.5 contracts as q3.
    g_yes_rows = [(q, c) for q, c in rows if c.direction == "GEMINI_YES_KALSHI_NO"]
    assert [q for q, _ in g_yes_rows] == [1, 2]
    assert all(c.quantity == D(q) for q, c in g_yes_rows)


def test_quote_skew_gate_fails_closed():
    g = TopOfBook("gemini", "G", 1000, D("0.6"), D("5"), D("0.4"), D("5"))
    k = TopOfBook("kalshi", "K", 1200, D("0.7"), D("5"), D("0.3"), D("5"))
    assert _small_q_candidates(
        g,
        k,
        kalshi_fee=zero_fee,
        max_quote_skew_ms=100,
        capital_limit=D("5"),
    ) == []

from decimal import Decimal

from src.gemini_kalshi_execution_shadow import TopOfBook
from src import gemini_kalshi_xrp_maker_triggered_canary as triggered

D = Decimal


def top(venue: str, ts: int) -> TopOfBook:
    return TopOfBook(
        venue=venue,
        market_id="m",
        ts_ms=ts,
        yes_bid=D("0.40"),
        yes_bid_size=D("2"),
        yes_ask=D("0.60"),
        yes_ask_size=D("2"),
    )


def test_pair_fresh_accepts_exact_50ms_skew():
    g = top("gemini", 1000)
    k = top("kalshi", 1050)
    assert triggered.pair_fresh(g, k, now_ms=1100)


def test_pair_fresh_rejects_51ms_skew():
    g = top("gemini", 1000)
    k = top("kalshi", 1051)
    assert not triggered.pair_fresh(g, k, now_ms=1100)


def test_pair_fresh_rejects_old_quote():
    g = top("gemini", 1000)
    k = top("kalshi", 1020)
    assert not triggered.pair_fresh(g, k, now_ms=1300)


def test_pair_fresh_rejects_future_timestamp():
    g = top("gemini", 1100)
    k = top("kalshi", 1110)
    assert not triggered.pair_fresh(g, k, now_ms=1099)

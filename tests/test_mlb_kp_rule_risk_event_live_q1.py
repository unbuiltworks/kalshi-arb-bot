# Production deployment gate for Polymarket-triggered exact-Kalshi routing.
import asyncio
from decimal import Decimal

from src.mlb_kp_rule_risk_event_live_q1 import ExactKalshiLimiter, MAX_TRIGGER_AGE_MS, _top_book


class Quote:
    yes_ask = Decimal("0.49")
    yes_depth = Decimal("2")
    no_ask = Decimal("0.52")
    no_depth = Decimal("3")


def test_trigger_freshness_never_exceeds_original_100ms_gate():
    assert MAX_TRIGGER_AGE_MS <= 100


def test_ws_quote_converts_without_midprice_math():
    book = _top_book(Quote())
    assert book.yes_ask == Decimal("0.49")
    assert book.yes_depth == Decimal("2")
    assert book.no_ask == Decimal("0.52")
    assert book.no_depth == Decimal("3")


def test_exact_kalshi_limiter_suppresses_immediate_repeat():
    async def check():
        limiter = ExactKalshiLimiter()
        assert await limiter.allow("KXMLBK-TEST") is True
        assert await limiter.allow("KXMLBK-TEST") is False
        assert limiter.allowed == 1
        assert limiter.global_suppressed + limiter.ticker_suppressed == 1
    asyncio.run(check())

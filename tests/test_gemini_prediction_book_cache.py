import asyncio
from decimal import Decimal as D
import time

import pytest

from src.gemini_prediction_book_cache import GeminiTopCache


def test_snapshot_reports_age_and_sequence():
    cache = GeminiTopCache("GEMI-BTC05M-TEST-UP", stale_ms=750)
    now = time.monotonic_ns()
    cache._top = (D("0.41"), D("100"), D("0.54"), D("120"))
    cache._received_ns = now - 25_000_000
    cache._sequence = 7
    snap = cache.snapshot(now_ns=now)
    assert snap is not None
    assert snap.as_top() == (D("0.41"), D("100"), D("0.54"), D("120"))
    assert 24.9 <= snap.age_ms <= 25.1
    assert snap.sequence == 7


def test_wait_fresh_fails_closed_on_stale_book():
    async def scenario():
        cache = GeminiTopCache("GEMI-BTC05M-TEST-UP", stale_ms=5)
        cache._top = (D("0.41"), D("100"), D("0.54"), D("120"))
        cache._received_ns = time.monotonic_ns() - 100_000_000
        cache._sequence = 1
        with pytest.raises(RuntimeError, match="no fresh persistent Gemini depth"):
            await cache.wait_fresh(timeout=0.02, max_age_ms=5)
    asyncio.run(scenario())


def test_wait_fresh_returns_immediate_current_book():
    async def scenario():
        cache = GeminiTopCache("GEMI-BTC05M-TEST-UP", stale_ms=750)
        cache._top = (D("0.42"), D("90"), D("0.53"), D("80"))
        cache._received_ns = time.monotonic_ns()
        cache._sequence = 3
        snap = await cache.wait_fresh(timeout=0.05, max_age_ms=500)
        assert snap.bid == D("0.42")
        assert snap.ask == D("0.53")
        assert snap.sequence == 3
    asyncio.run(scenario())

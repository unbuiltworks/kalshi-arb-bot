from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from src.gemini_btc5m_fresh_depth_retry import reliable_fresh_top


def test_retries_then_succeeds(monkeypatch):
    async def scenario():
        calls = {"n": 0}
        expected = (
            Decimal("0.41"),
            Decimal("100"),
            Decimal("0.58"),
            Decimal("100"),
        )

        async def flaky(symbol: str, timeout: float):
            calls["n"] += 1
            assert symbol == "GEMI-BTC05M-TEST-UP"
            assert timeout == 4.0
            if calls["n"] < 3:
                raise RuntimeError("no fresh Gemini BTC5m depth")
            return expected

        async def no_sleep(_seconds: float):
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        got = await reliable_fresh_top(flaky, "GEMI-BTC05M-TEST-UP")
        assert got == expected
        assert calls["n"] == 3

    asyncio.run(scenario())


def test_fails_closed_after_three_attempts(monkeypatch):
    async def scenario():
        calls = {"n": 0}

        async def always_fail(_symbol: str, timeout: float):
            calls["n"] += 1
            assert timeout == 4.0
            raise RuntimeError("no fresh Gemini BTC5m depth")

        async def no_sleep(_seconds: float):
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        with pytest.raises(RuntimeError, match="after 3 independent attempts"):
            await reliable_fresh_top(always_fail, "GEMI-BTC05M-TEST-UP")
        assert calls["n"] == 3

    asyncio.run(scenario())


def test_argument_validation():
    async def never_called(_symbol: str, timeout: float):
        raise AssertionError(timeout)

    with pytest.raises(ValueError, match="attempts"):
        asyncio.run(reliable_fresh_top(never_called, "X", attempts=0))
    with pytest.raises(ValueError, match="timeout_seconds"):
        asyncio.run(reliable_fresh_top(never_called, "X", timeout_seconds=0))
    with pytest.raises(ValueError, match="retry_delay_seconds"):
        asyncio.run(reliable_fresh_top(never_called, "X", retry_delay_seconds=-1))

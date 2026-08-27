from __future__ import annotations

from decimal import Decimal

import pytest

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1_v2 as mod


@pytest.mark.asyncio
async def test_reliable_fresh_top_retries_then_succeeds(monkeypatch):
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
        assert timeout == mod._ATTEMPT_TIMEOUT_SECONDS
        if calls["n"] < 3:
            raise RuntimeError("no fresh Gemini BTC5m depth")
        return expected

    async def no_sleep(_seconds: float):
        return None

    monkeypatch.setattr(mod, "_original_fresh_top", flaky)
    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)

    got = await mod.reliable_fresh_top("GEMI-BTC05M-TEST-UP")
    assert got == expected
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_reliable_fresh_top_fails_closed_after_three_attempts(monkeypatch):
    calls = {"n": 0}

    async def always_fail(symbol: str, timeout: float):
        calls["n"] += 1
        raise RuntimeError("no fresh Gemini BTC5m depth")

    async def no_sleep(_seconds: float):
        return None

    monkeypatch.setattr(mod, "_original_fresh_top", always_fail)
    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)

    with pytest.raises(RuntimeError, match="after 3 independent attempts"):
        await mod.reliable_fresh_top("GEMI-BTC05M-TEST-UP")

    assert calls["n"] == 3

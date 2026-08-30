import asyncio
from decimal import Decimal as D

from src import gemini_low_volume_market_scan as scan


def row():
    return {
        "ticker": "BTC05MTEST",
        "symbol": "GEMI-BTC05MTEST-UP",
        "asset": "BTC",
        "window_minutes": 5,
        "daily_pool_usd": "250",
        "qualifying_maker_count": 0,
        "seconds_to_expiry": 300,
    }


def test_sample_retries_transient_depth_failure(monkeypatch):
    calls = 0

    async def fake_fresh_top(_symbol, timeout):
        nonlocal calls
        calls += 1
        assert timeout == scan.SAMPLE_TIMEOUT_S
        if calls == 1:
            raise RuntimeError("transient")
        return D("0.48"), D("10"), D("0.50"), D("10")

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(scan.base, "fresh_top", fake_fresh_top)
    monkeypatch.setattr(scan.asyncio, "sleep", no_wait)
    out = asyncio.run(scan._sample(row()))
    assert out["book_ok"] is True
    assert out["sample_attempts"] == 2
    assert calls == 2
    assert D(out["model"]["modeled_pair_profit_after_fees"]) == D("0.10")


def test_sample_stops_after_bounded_attempts(monkeypatch):
    calls = 0

    async def always_fail(_symbol, timeout):
        nonlocal calls
        calls += 1
        raise RuntimeError("still unavailable")

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(scan.base, "fresh_top", always_fail)
    monkeypatch.setattr(scan.asyncio, "sleep", no_wait)
    out = asyncio.run(scan._sample(row()))
    assert out["book_ok"] is False
    assert out["sample_attempts"] == scan.SAMPLE_ATTEMPTS
    assert calls == scan.SAMPLE_ATTEMPTS
    assert out["error_type"] == "RuntimeError"

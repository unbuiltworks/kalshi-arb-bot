import asyncio
from pathlib import Path

import pytest

import src.gemini_kalshi_aligned_watch_supervisor as supervisor


def test_rejects_cycles_that_can_span_full_15m_window(tmp_path: Path):
    with pytest.raises(ValueError, match="cycle_seconds must be < 900"):
        asyncio.run(
            supervisor.supervise(
                cycle_seconds=900,
                poll_s=1,
                max_window_skew_s=30,
                max_quote_skew_ms=500,
                output=tmp_path / "watch.jsonl",
                retry_delay_s=0,
                max_cycles=1,
            )
        )


def test_supervisor_rediscover_cycles_and_survives_transient_failure(monkeypatch, tmp_path: Path):
    calls = []

    async def fake_watch(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("transient")

    monkeypatch.setattr(supervisor, "watch", fake_watch)
    output = tmp_path / "watch.jsonl"

    asyncio.run(
        supervisor.supervise(
            cycle_seconds=120,
            poll_s=1,
            max_window_skew_s=30,
            max_quote_skew_ms=500,
            output=output,
            retry_delay_s=0,
            max_cycles=3,
        )
    )

    assert len(calls) == 3
    assert all(call["seconds"] == 120 for call in calls)
    assert all(call["output"] == output for call in calls)


def test_cancelled_error_propagates(monkeypatch, tmp_path: Path):
    async def fake_watch(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(supervisor, "watch", fake_watch)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            supervisor.supervise(
                cycle_seconds=120,
                poll_s=1,
                max_window_skew_s=30,
                max_quote_skew_ms=500,
                output=tmp_path / "watch.jsonl",
                retry_delay_s=0,
                max_cycles=1,
            )
        )

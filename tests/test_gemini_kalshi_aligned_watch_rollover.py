import asyncio
from pathlib import Path

import pytest

from src.gemini_kalshi_aligned_watch_rollover import run_rollover, segment_seconds


def test_segment_seconds_bounds_to_remaining():
    assert segment_seconds(remaining_s=10.0, rediscover_s=60.0) == 10.0
    assert segment_seconds(remaining_s=120.0, rediscover_s=60.0) == 60.0
    assert segment_seconds(remaining_s=0.0, rediscover_s=60.0) == 0.0


def test_segment_seconds_rejects_nonpositive_rediscovery():
    with pytest.raises(ValueError):
        segment_seconds(remaining_s=1.0, rediscover_s=0.0)


def test_run_rollover_reinvokes_watcher_with_same_read_only_parameters(monkeypatch):
    calls = []
    ticks = iter([0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 3.1])

    monkeypatch.setattr(
        "src.gemini_kalshi_aligned_watch_rollover.time.monotonic",
        lambda: next(ticks),
    )

    async def fake_watch(**kwargs):
        calls.append(kwargs)

    asyncio.run(
        run_rollover(
            seconds=3.0,
            rediscover_s=1.0,
            poll_s=0.25,
            max_window_skew_s=30.0,
            max_quote_skew_ms=500,
            output=Path("logs/test.jsonl"),
            watch_fn=fake_watch,
        )
    )

    assert len(calls) == 3
    assert [call["seconds"] for call in calls] == [1.0, 1.0, 1.0]
    assert all(call["poll_s"] == 0.25 for call in calls)
    assert all(call["max_window_skew_s"] == 30.0 for call in calls)
    assert all(call["max_quote_skew_ms"] == 500 for call in calls)
    assert all(call["output"] == Path("logs/test.jsonl") for call in calls)


def test_run_rollover_rejects_invalid_runtime_values():
    async def fake_watch(**kwargs):
        raise AssertionError("watcher should not be called")

    with pytest.raises(ValueError):
        asyncio.run(
            run_rollover(
                seconds=0.0,
                rediscover_s=60.0,
                poll_s=1.0,
                max_window_skew_s=30.0,
                max_quote_skew_ms=500,
                output=Path("logs/test.jsonl"),
                watch_fn=fake_watch,
            )
        )

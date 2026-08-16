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


def test_run_rollover_reinvokes_watcher_with_same_read_only_parameters():
    calls = []
    ticks = iter([0.0, 0.0, 1.0, 2.0, 3.1])

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
            clock_fn=lambda: next(ticks),
        )
    )

    assert len(calls) == 3
    assert [call["seconds"] for call in calls] == [1.0, 1.0, 1.0]
    assert all(call["poll_s"] == 0.25 for call in calls)
    assert all(call["max_window_skew_s"] == 30.0 for call in calls)
    assert all(call["max_quote_skew_ms"] == 500 for call in calls)
    assert all(call["output"] == Path("logs/test.jsonl") for call in calls)


def test_run_rollover_recovers_after_transient_segment_error():
    calls = []
    sleeps = []
    ticks = iter([0.0, 0.0, 0.1, 1.0, 2.1])

    async def flaky_watch(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("temporary public API failure")

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    asyncio.run(
        run_rollover(
            seconds=2.0,
            rediscover_s=1.0,
            poll_s=0.25,
            max_window_skew_s=30.0,
            max_quote_skew_ms=500,
            output=Path("logs/test.jsonl"),
            error_backoff_s=0.5,
            watch_fn=flaky_watch,
            sleep_fn=fake_sleep,
            clock_fn=lambda: next(ticks),
        )
    )

    assert len(calls) == 2
    assert sleeps == [0.5]
    assert all(call["output"] == Path("logs/test.jsonl") for call in calls)


def test_run_rollover_propagates_cancellation():
    ticks = iter([0.0, 0.0])

    async def cancelled_watch(**kwargs):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            run_rollover(
                seconds=10.0,
                rediscover_s=1.0,
                poll_s=0.25,
                max_window_skew_s=30.0,
                max_quote_skew_ms=500,
                output=Path("logs/test.jsonl"),
                watch_fn=cancelled_watch,
                clock_fn=lambda: next(ticks),
            )
        )


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

    with pytest.raises(ValueError):
        asyncio.run(
            run_rollover(
                seconds=60.0,
                rediscover_s=60.0,
                poll_s=1.0,
                max_window_skew_s=30.0,
                max_quote_skew_ms=500,
                output=Path("logs/test.jsonl"),
                error_backoff_s=-1.0,
                watch_fn=fake_watch,
            )
        )

from pathlib import Path

import pytest

from src.gemini_kalshi_aligned_supervisor import supervise


@pytest.mark.asyncio
async def test_supervisor_restarts_watch_for_rollover(monkeypatch, tmp_path: Path):
    calls = []
    now = {"t": 0.0}

    def fake_monotonic():
        return now["t"]

    async def fake_watch(**kwargs):
        calls.append(kwargs)
        now["t"] += kwargs["seconds"]

    monkeypatch.setattr("src.gemini_kalshi_aligned_supervisor.time.monotonic", fake_monotonic)
    output = tmp_path / "watch.jsonl"
    summary = await supervise(
        total_seconds=125.0,
        segment_seconds=60.0,
        poll_s=1.0,
        max_window_skew_s=2.0,
        max_quote_skew_ms=500,
        output=output,
        watch_fn=fake_watch,
    )

    assert [call["seconds"] for call in calls] == [60.0, 60.0, 5.0]
    assert all(call["output"] == output for call in calls)
    assert summary["segments"] == 3
    assert summary["read_only"] is True
    assert summary["order_entry"] is False


@pytest.mark.asyncio
async def test_supervisor_rejects_nonpositive_runtime(tmp_path: Path):
    with pytest.raises(ValueError):
        await supervise(
            total_seconds=0,
            segment_seconds=60,
            poll_s=1,
            max_window_skew_s=2,
            max_quote_skew_ms=500,
            output=tmp_path / "watch.jsonl",
        )


@pytest.mark.asyncio
async def test_supervisor_rejects_nonpositive_segment(tmp_path: Path):
    with pytest.raises(ValueError):
        await supervise(
            total_seconds=60,
            segment_seconds=0,
            poll_s=1,
            max_window_skew_s=2,
            max_quote_skew_ms=500,
            output=tmp_path / "watch.jsonl",
        )

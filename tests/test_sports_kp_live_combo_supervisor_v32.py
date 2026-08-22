from __future__ import annotations

import pytest

from src import sports_kp_live_combo_supervisor_v32 as v32


def test_feedback_summary_reports_best_lock_without_execution_change():
    summary = v32._feedback_summary({
        "A": {"best_lock": "0.01", "last_lock": "-0.02", "observations": 4, "quoted": 2},
        "B": {"best_lock": "0.025", "last_lock": "0.02", "observations": 3, "quoted": 3},
    }, 2)
    assert summary["rfq_signatures_in_memory"] == 2
    assert summary["rfq_signatures_updated_this_cycle"] == 2
    assert summary["best_conservative_lock"] == "0.025"
    assert summary["best_signature"] == "B"
    assert summary["execution_semantics_changed"] is False


@pytest.mark.asyncio
async def test_merge_telemetry_is_best_effort(monkeypatch):
    calls = []

    async def fake_merge(before, after):
        return 1

    async def broken_emit(metadata):
        calls.append(metadata)
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(v32, "_ORIGINAL_MERGE", fake_merge)
    monkeypatch.setattr(v32.telemetry, "emit_cycle", broken_emit)
    result = await v32._merge_with_telemetry({}, {
        "X": {"best_lock": "0.03", "last_lock": "0.03", "observations": 1, "quoted": 1}
    })
    assert result == 1
    assert calls[0]["best_conservative_lock"] == "0.03"

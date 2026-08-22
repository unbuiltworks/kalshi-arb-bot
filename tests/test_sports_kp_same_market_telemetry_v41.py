from __future__ import annotations

import pytest

from src import sports_kp_live_combo_supervisor_v37 as v37


@pytest.mark.asyncio
async def test_same_market_telemetry_is_diagnostic_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def fake_emit_cycle(metadata):
        captured.update(metadata)
        return True

    monkeypatch.setattr(v37.telemetry, "emit_cycle", fake_emit_cycle)
    await v37._emit_same_market_telemetry({
        "events_scanned": 12,
        "markets_scanned": 40,
        "priced_binary_markets": 18,
        "shadow_executable": 2,
        "best": {
            "ticker": "TEST",
            "locked_profit": "0.04",
            "all_in_cost": "0.96",
            "yes_depth_fp": "3",
            "no_depth_fp": "4",
        },
    })

    assert captured["telemetry_kind"] == "SAME_MARKET_YES_NO_SHADOW"
    assert captured["shadow_executable"] == 2
    assert captured["best_locked_profit"] == "0.04"
    assert captured["same_venue_exact_partition"] is True
    assert captured["count_as_realized"] is False
    assert captured["order_entry"] is False


@pytest.mark.asyncio
async def test_same_market_telemetry_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(_metadata):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(v37.telemetry, "emit_cycle", fail)
    await v37._emit_same_market_telemetry({"best": None})

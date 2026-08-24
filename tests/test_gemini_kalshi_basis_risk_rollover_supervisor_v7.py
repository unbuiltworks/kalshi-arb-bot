from __future__ import annotations

import asyncio

import pytest

from src import gemini_kalshi_basis_risk_rollover_supervisor_v7 as v7


def test_guarded_asset_loop_escalates_unpaired_incident(monkeypatch):
    async def fake_asset_loop(asset: str, lease_minutes: int) -> None:
        return None

    monkeypatch.setattr(v7.v6, "_asset_loop", fake_asset_loop)
    monkeypatch.setattr(v7.v6, "_state", lambda: {"inflight_or_unpaired": [{"trade_id": "incident-1"}]})
    monkeypatch.setattr(
        v7.child_v6,
        "automatic_rollover_allowed",
        lambda state: (False, v7.GLOBAL_INCIDENT_REASON),
    )
    monkeypatch.setattr(v7.v6, "_log_state", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="global execution incident stop"):
        asyncio.run(v7._guarded_asset_loop("ETH", 120))


def test_guarded_asset_loop_allows_clean_terminal_return(monkeypatch):
    async def fake_asset_loop(asset: str, lease_minutes: int) -> None:
        return None

    monkeypatch.setattr(v7.v6, "_asset_loop", fake_asset_loop)
    monkeypatch.setattr(v7.v6, "_state", lambda: {"inflight_or_unpaired": []})
    monkeypatch.setattr(
        v7.child_v6,
        "automatic_rollover_allowed",
        lambda state: (False, "NEW_OR_UNREVIEWED_REALIZED_LOSS_HARD_STOP"),
    )

    asyncio.run(v7._guarded_asset_loop("SOL", 120))


def test_main_refuses_boot_with_unpaired_incident(monkeypatch):
    monkeypatch.setattr(v7.v6, "_validate_outer_authorization", lambda: None)
    monkeypatch.setattr(v7.v6, "_lease_minutes", lambda: 120)
    monkeypatch.setattr(v7.v6, "_state", lambda: {"inflight_or_unpaired": [{"trade_id": "incident-1"}]})
    monkeypatch.setattr(
        v7.child_v6,
        "automatic_rollover_allowed",
        lambda state: (False, v7.GLOBAL_INCIDENT_REASON),
    )
    monkeypatch.setattr(v7.v6, "_log_state", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="refuses boot with unresolved execution incident"):
        asyncio.run(v7.main_async())

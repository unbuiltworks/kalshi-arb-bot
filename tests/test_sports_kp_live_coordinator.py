from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import time

import pytest

from src.sports_kp_execution import ExecutionLeg, KPExecutionPlan, VenueResult
from src.sports_kp_live_coordinator import execute_after_fresh_revalidation

D = Decimal


def _plan(*, age_ms: int = 0, profit: str = "0.05") -> KPExecutionPlan:
    return KPExecutionPlan(
        settlement_key="k",
        unit_profit=D(profit),
        quote_skew_ms=10,
        created_ms=int(time.time() * 1000) - age_ms,
        poly=ExecutionLeg("polymarket", "poly", "YES", "YES", D("0.40"), D("0.40"), "YES"),
        kalshi=ExecutionLeg("kalshi", "kal", "NO", "YES", D("0.50"), D("0.50"), "NO"),
        rule_fingerprint_sha256="a" * 64,
    )


class FakePoly:
    def __init__(self):
        self.sent = 0
        self.preflighted = 0

    async def preflight(self, leg):
        self.preflighted += 1

    async def place_fok(self, leg):
        self.sent += 1
        return VenueResult("polymarket", "p", "FILLED", D("1"), D("0.40"), None)

    async def emergency_flatten(self, leg):
        return VenueResult("polymarket", None, "FLATTENED", D("1"), None, None, True)

    def close(self):
        pass


class FakeKalshi:
    def __init__(self):
        self.sent = 0
        self.preflighted = 0

    async def preflight(self, leg):
        self.preflighted += 1

    async def place_fok(self, leg):
        self.sent += 1
        return VenueResult("kalshi", "k", "FILLED", D("1"), D("0.50"), None)

    async def emergency_flatten(self, leg):
        return VenueResult("kalshi", None, "FLATTENED", D("1"), None, None, True)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_final_snapshot_is_requested_after_preflight(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENABLE_SPORTS_KP_LIVE", "YES")
    token = tmp_path / "arm"
    token.write_text("ARM")
    poly, kalshi = FakePoly(), FakeKalshi()
    observed = []

    async def fresh():
        observed.append((poly.preflighted, kalshi.preflighted))
        return _plan()

    result = await execute_after_fresh_revalidation(
        _plan(age_ms=5000), fresh, arm_token=token, live_requested=True,
        poly=poly, kalshi=kalshi,
    )
    assert observed == [(1, 1)]
    assert result["outcome"] == "BOTH_FILLED"
    assert result["post_preflight_revalidated"] is True
    assert not token.exists()


@pytest.mark.asyncio
async def test_stale_final_snapshot_never_consumes_arm_or_sends(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENABLE_SPORTS_KP_LIVE", "YES")
    token = tmp_path / "arm"
    token.write_text("ARM")
    poly, kalshi = FakePoly(), FakeKalshi()

    async def stale():
        return _plan(age_ms=1000)

    with pytest.raises(PermissionError, match="stale"):
        await execute_after_fresh_revalidation(
            _plan(), stale, arm_token=token, live_requested=True,
            poly=poly, kalshi=kalshi,
        )
    assert token.exists()
    assert poly.sent == 0 and kalshi.sent == 0


@pytest.mark.asyncio
async def test_changed_instrument_never_consumes_arm(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENABLE_SPORTS_KP_LIVE", "YES")
    token = tmp_path / "arm"
    token.write_text("ARM")
    poly, kalshi = FakePoly(), FakeKalshi()

    async def changed():
        return replace(_plan(), settlement_key="different")

    with pytest.raises(PermissionError, match="changed"):
        await execute_after_fresh_revalidation(
            _plan(), changed, arm_token=token, live_requested=True,
            poly=poly, kalshi=kalshi,
        )
    assert token.exists()
    assert poly.sent == 0 and kalshi.sent == 0

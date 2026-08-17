from __future__ import annotations

import os
from pathlib import Path
import time
from decimal import Decimal

import pytest

from src.sports_kp_execution import (
    ExecutionLeg,
    KPExecutionPlan,
    RuleApproval,
    VenueResult,
    build_kp_plan,
    execute_kp_plan,
)
from src.three_venue_pricing_core import PricedQuote
from src.universal_prediction_quant import LockOpportunity, MarketQuote

D = Decimal


def _priced(venue: str, market_id: str, source_side: str, yes: str, no: str, key: str, ts: int) -> PricedQuote:
    q = MarketQuote(
        venue=venue,
        market_id=f"{market_id}#{source_side}",
        settlement_key=key,
        category="sports_mlb_game",
        yes_cost=D(yes) + D("0.01"),
        no_cost=D(no) + D("0.01"),
        yes_depth=D("1"),
        no_depth=D("1"),
        received_ms=ts,
        settlement_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    )
    return PricedQuote(q, D(yes), D(no), D("0.01"), D("0.01"), "test", source_side)


def _opp(key: str, yes_venue: str = "polymarket", no_venue: str = "kalshi") -> LockOpportunity:
    return LockOpportunity(
        settlement_key=key,
        category="sports_mlb_game",
        yes_venue=yes_venue,
        yes_market_id="poly#YES" if yes_venue == "polymarket" else "kal#YES",
        no_venue=no_venue,
        no_market_id="kal#YES" if no_venue == "kalshi" else "poly#YES",
        unit_cost=D("0.95"),
        unit_profit=D("0.05"),
        max_quantity=D("1"),
        max_capital=D("0.95"),
        max_locked_profit=D("0.05"),
        quote_skew_ms=10,
        settlement_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
        expected_settlement_minutes=None,
        capital_velocity_score=D("0.05"),
    )


def _approval(key: str) -> RuleApproval:
    return RuleApproval(
        settlement_key=key,
        polymarket_slug="poly",
        kalshi_ticker="kal",
        polymarket_source_side="YES",
        kalshi_source_side="NO",
        rule_fingerprint_sha256="a" * 64,
        approved_for_execution=True,
    )


def test_build_plan_respects_native_side_orientation():
    now = int(time.time() * 1000)
    rows = [
        _priced("polymarket", "poly", "YES", "0.40", "0.60", "k", now),
        _priced("kalshi", "kal", "NO", "0.50", "0.50", "k", now - 5),
    ]
    plan = build_kp_plan(_opp("k"), rows, _approval("k"), now_ms=now)
    assert plan.poly.native_outcome == "YES"
    # Normalized NO on a source_side=NO Kalshi contract is native YES.
    assert plan.kalshi.normalized_outcome == "NO"
    assert plan.kalshi.native_outcome == "YES"
    assert plan.kalshi.yes_book_limit == D("0.50")


def test_build_plan_refuses_nonapproved_rules():
    now = int(time.time() * 1000)
    rows = [
        _priced("polymarket", "poly", "YES", "0.40", "0.60", "k", now),
        _priced("kalshi", "kal", "NO", "0.50", "0.50", "k", now),
    ]
    approval = _approval("k")
    approval = RuleApproval(**{**approval.__dict__, "approved_for_execution": False})
    with pytest.raises(PermissionError):
        build_kp_plan(_opp("k"), rows, approval, now_ms=now)


class FakePoly:
    def __init__(self, result: VenueResult):
        self.result = result
        self.flattened = False
        self.preflighted = False

    async def preflight(self, leg):
        self.preflighted = True

    async def place_fok(self, leg):
        return self.result

    async def emergency_flatten(self, leg):
        self.flattened = True
        return VenueResult("polymarket", None, "FLATTENED", D("1"), None, None, True)

    def close(self):
        pass


class FakeKalshi:
    def __init__(self, result: VenueResult):
        self.result = result
        self.flattened = False
        self.preflighted = False

    async def preflight(self, leg):
        self.preflighted = True

    async def place_fok(self, leg):
        return self.result

    async def emergency_flatten(self, leg):
        self.flattened = True
        return VenueResult("kalshi", None, "FLATTENED", D("1"), None, None, True)

    async def close(self):
        pass


def _plan() -> KPExecutionPlan:
    now = int(time.time() * 1000)
    return KPExecutionPlan(
        settlement_key="k",
        unit_profit=D("0.05"),
        quote_skew_ms=10,
        created_ms=now,
        poly=ExecutionLeg("polymarket", "poly", "YES", "YES", D("0.40"), D("0.40"), "YES"),
        kalshi=ExecutionLeg("kalshi", "kal", "NO", "YES", D("0.50"), D("0.50"), "NO"),
        rule_fingerprint_sha256="a" * 64,
    )


@pytest.mark.asyncio
async def test_both_filled_consumes_one_use_arm(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENABLE_SPORTS_KP_LIVE", "YES")
    token = tmp_path / "arm"
    token.write_text("ARM", encoding="utf-8")
    poly = FakePoly(VenueResult("polymarket", "p1", "FILLED", D("1"), D("0.40"), None))
    kalshi = FakeKalshi(VenueResult("kalshi", "k1", "FILLED", D("1"), D("0.50"), None))
    result = await execute_kp_plan(_plan(), arm_token=token, live_requested=True, poly=poly, kalshi=kalshi)
    assert result["outcome"] == "BOTH_FILLED"
    assert result["hard_halt_required"] is False
    assert not token.exists()
    assert poly.preflighted and kalshi.preflighted


@pytest.mark.asyncio
async def test_one_leg_fill_flattens_and_hard_halts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENABLE_SPORTS_KP_LIVE", "YES")
    token = tmp_path / "arm"
    token.write_text("ARM", encoding="utf-8")
    poly = FakePoly(VenueResult("polymarket", "p1", "FILLED", D("1"), D("0.40"), None))
    kalshi = FakeKalshi(VenueResult("kalshi", "k1", "CANCELED", D("0"), None, "FOK_NOT_FILLED"))
    result = await execute_kp_plan(_plan(), arm_token=token, live_requested=True, poly=poly, kalshi=kalshi)
    assert result["outcome"] == "ONE_LEG_FILLED"
    assert result["hard_halt_required"] is True
    assert poly.flattened is True
    assert result["flatten"]["status"] == "FLATTENED"


@pytest.mark.asyncio
async def test_unknown_submission_hard_halts_without_second_attempt(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENABLE_SPORTS_KP_LIVE", "YES")
    token = tmp_path / "arm"
    token.write_text("ARM", encoding="utf-8")
    poly = FakePoly(VenueResult("polymarket", None, "UNKNOWN", D("0"), None, "timeout"))
    kalshi = FakeKalshi(VenueResult("kalshi", "k1", "CANCELED", D("0"), None, "FOK_NOT_FILLED"))
    result = await execute_kp_plan(_plan(), arm_token=token, live_requested=True, poly=poly, kalshi=kalshi)
    assert result["outcome"] == "AMBIGUOUS_SUBMISSION"
    assert result["hard_halt_required"] is True
    assert not token.exists()

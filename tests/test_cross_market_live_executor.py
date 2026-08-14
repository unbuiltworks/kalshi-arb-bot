from decimal import Decimal

import pytest

from src import polymarket_kalshi_shadow as calc
from src.cross_market_live_executor import (
    ExecutablePair,
    LiveCrossMarketExecutor,
    LiveGate,
    VenueFill,
)


class FakeVenue:
    def __init__(self, venue: str, filled: int):
        self.venue = venue
        self.filled = filled
        self.previews = 0
        self.submits = 0

    async def preview(self, **kwargs):
        self.previews += 1
        return {"ok": True}

    async def submit_fok(self, *, quantity: int, **kwargs):
        self.submits += 1
        return VenueFill(self.venue, quantity, self.filled)


def plan() -> calc.ShadowPlan:
    return calc.ShadowPlan(
        direction="POLY_OVER_KALSHI_UNDER",
        quantity=3,
        poly_side="OVER",
        poly_price=Decimal("0.30"),
        kalshi_side="UNDER",
        kalshi_price=Decimal("0.65"),
        poly_fee=Decimal("0.03"),
        kalshi_fee=Decimal("0.03"),
        total_cost=Decimal("2.91"),
        payout=Decimal("3.00"),
        profit=Decimal("0.09"),
        roi=Decimal("0.0309"),
    )


def pair(status="FULL_RULES_AUDITED_EXECUTABLE") -> ExecutablePair:
    return ExecutablePair("p", "poly", "kalshi", status)


def armed_gate() -> LiveGate:
    return LiveGate(True, "I_ACCEPT_LEGGING_RISK", Decimal("10"), Decimal("0.05"))


@pytest.mark.asyncio
async def test_disabled_gate_never_previews_or_submits():
    poly = FakeVenue("polymarket", 3)
    kalshi = FakeVenue("kalshi", 3)
    recovered = []

    async def recovery(*args):
        recovered.append(args)

    ex = LiveCrossMarketExecutor(
        poly=poly,
        kalshi=kalshi,
        recovery=recovery,
        gate=LiveGate(False, "", Decimal("10"), Decimal("0.05")),
    )
    with pytest.raises(RuntimeError, match="disabled"):
        await ex.execute(pair(), plan())
    assert poly.previews == kalshi.previews == 0
    assert poly.submits == kalshi.submits == 0
    assert not recovered


@pytest.mark.asyncio
async def test_noncertified_pair_is_rejected_before_venue_touch():
    poly = FakeVenue("polymarket", 3)
    kalshi = FakeVenue("kalshi", 3)

    async def recovery(*args):
        raise AssertionError("should not recover")

    ex = LiveCrossMarketExecutor(poly=poly, kalshi=kalshi, recovery=recovery, gate=armed_gate())
    with pytest.raises(RuntimeError, match="not settlement-certified"):
        await ex.execute(pair("FULL_RULES_AUDITED_SHADOW"), plan())
    assert poly.previews == kalshi.previews == 0
    assert poly.submits == kalshi.submits == 0


@pytest.mark.asyncio
async def test_both_fok_fills_complete_without_recovery():
    poly = FakeVenue("polymarket", 3)
    kalshi = FakeVenue("kalshi", 3)
    recovered = []

    async def recovery(*args):
        recovered.append(args)

    ex = LiveCrossMarketExecutor(poly=poly, kalshi=kalshi, recovery=recovery, gate=armed_gate())
    out = await ex.execute(pair(), plan())
    assert out.status == "BOTH_FILLED"
    assert ex.halted is False
    assert poly.previews == kalshi.previews == 1
    assert poly.submits == kalshi.submits == 1
    assert not recovered


@pytest.mark.asyncio
async def test_one_sided_fill_triggers_recovery_and_permanent_halt():
    poly = FakeVenue("polymarket", 3)
    kalshi = FakeVenue("kalshi", 0)
    recovered = []

    async def recovery(*args):
        recovered.append(args)

    ex = LiveCrossMarketExecutor(poly=poly, kalshi=kalshi, recovery=recovery, gate=armed_gate())
    out = await ex.execute(pair(), plan())
    assert out.status == "LEGGING_RECOVERY_TRIGGERED"
    assert out.recovery_attempted is True
    assert ex.halted is True
    assert len(recovered) == 1

    # A legging incident requires an operator/restart before another trade.
    with pytest.raises(RuntimeError, match="halted"):
        await ex.execute(pair(), plan())
    assert poly.submits == kalshi.submits == 1


@pytest.mark.asyncio
async def test_recovery_failure_still_leaves_executor_halted():
    poly = FakeVenue("polymarket", 3)
    kalshi = FakeVenue("kalshi", 0)

    async def recovery(*args):
        raise RuntimeError("flatten failed")

    ex = LiveCrossMarketExecutor(poly=poly, kalshi=kalshi, recovery=recovery, gate=armed_gate())
    with pytest.raises(RuntimeError, match="flatten failed"):
        await ex.execute(pair(), plan())
    assert ex.halted is True


@pytest.mark.asyncio
async def test_hard_10_dollar_ceiling_cannot_be_bypassed():
    poly = FakeVenue("polymarket", 3)
    kalshi = FakeVenue("kalshi", 3)

    async def recovery(*args):
        pass

    too_big = plan().__class__(
        direction=plan().direction,
        quantity=20,
        poly_side=plan().poly_side,
        poly_price=plan().poly_price,
        kalshi_side=plan().kalshi_side,
        kalshi_price=plan().kalshi_price,
        poly_fee=Decimal("0.10"),
        kalshi_fee=Decimal("0.10"),
        total_cost=Decimal("19.20"),
        payout=Decimal("20"),
        profit=Decimal("0.80"),
        roi=Decimal("0.0416"),
    )
    ex = LiveCrossMarketExecutor(poly=poly, kalshi=kalshi, recovery=recovery, gate=armed_gate())
    with pytest.raises(RuntimeError, match="exceeds live cap"):
        await ex.execute(pair(), too_big)

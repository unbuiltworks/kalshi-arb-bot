from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.kalshi_nested_live_probe_guard import NestedLiveProbeSpec
from src.kalshi_nested_probe_transport import AmbiguousOrderSubmission, OrderAck, OrderState
from src.kalshi_nested_seed_first_probe import execute_seed_first

D = Decimal
NY = ZoneInfo("America/New_York")


def _spec():
    return NestedLiveProbeSpec(
        event_ticker="KXETHD-26AUG1620",
        event_close=datetime(2026, 8, 16, 20, 0, tzinfo=NY),
        strike_type="greater",
        lower_ticker="KXETHD-26AUG1620-T1914.99",
        lower_strike=D("1914.99"),
        higher_ticker="KXETHD-26AUG1620-T1919.99",
        higher_strike=D("1919.99"),
        maker_ticker="KXETHD-26AUG1620-T1919.99",
        maker_side="no",
        maker_cost=D("0.95"),
        hedge_ticker="KXETHD-26AUG1620-T1914.99",
        hedge_side="yes",
        hedge_cost=D("0.01"),
        fee_type="quadratic",
        fee_multiplier=D("1"),
        target_profit=D("0.03"),
        maker_rest_seconds=1,
    )


def _state(order_id, client_id, ticker, fill, remaining, *, taker_cost="0", maker_cost="0", taker_fee="0"):
    return OrderState(
        order_id=order_id,
        client_order_id=client_id,
        ticker=ticker,
        fill_count=D(fill),
        remaining_count=D(remaining),
        taker_fill_cost=D(taker_cost),
        maker_fill_cost=D(maker_cost),
        taker_fees=D(taker_fee),
    )


class FailureTransport:
    def __init__(self, *, seed_mode="full", maker_mode="reject", reconcile_seed=None, reconcile_maker=None):
        self.seed_mode = seed_mode
        self.maker_mode = maker_mode
        self.reconcile_seed = reconcile_seed
        self.reconcile_maker = reconcile_maker
        self.created = []
        self.seed = _state("seed", "seed-c", "LOW", "1", "0", taker_cost="0.01", taker_fee="0.01")
        self.maker = _state("maker", "maker-c", "HIGH", "0", "1")

    async def create(self, payload):
        self.created.append(payload)
        is_seed = payload["time_in_force"] == "immediate_or_cancel"
        mode = self.seed_mode if is_seed else self.maker_mode
        if mode == "ambiguous":
            raise AmbiguousOrderSubmission(payload["client_order_id"])
        if mode == "reject":
            raise RuntimeError("known HTTP rejection")
        state = self.seed if is_seed else self.maker
        return OrderAck(state.order_id, state.client_order_id, state.fill_count, state.remaining_count, 1)

    async def reconcile_by_client_id(self, *, ticker, client_order_id):
        if "-h" in client_order_id:
            return self.reconcile_seed
        return self.reconcile_maker

    async def get(self, order_id):
        return self.seed if order_id == "seed" else self.maker

    async def cancel(self, order_id):
        if order_id == "maker":
            self.maker = OrderState(**{**self.maker.__dict__, "remaining_count": D("0")})
        return {"order_id": order_id}


@pytest.fixture(autouse=True)
def gates(monkeypatch):
    import src.kalshi_nested_live_probe_guard as guard
    class FrozenDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 8, 16, 19, 30, tzinfo=NY)
    monkeypatch.setattr(guard, "datetime", FrozenDateTime)
    monkeypatch.setattr("src.kalshi_nested_seed_first_probe.live_probe_authorized", lambda *a, **k: True)
    async def instant(_seconds):
        return None
    monkeypatch.setattr("src.kalshi_nested_seed_first_probe.asyncio.sleep", instant)


@pytest.mark.asyncio
async def test_known_seed_rejection_never_submits_maker():
    tx = FailureTransport(seed_mode="reject")
    result = await execute_seed_first(_spec(), cli_live=True, transport=tx)
    assert result.status == "SEED_REJECTED_STOP"
    assert result.actual_worst_case_pnl == D("0")
    assert len(tx.created) == 1


@pytest.mark.asyncio
async def test_unresolved_seed_ambiguity_never_submits_maker():
    tx = FailureTransport(seed_mode="ambiguous", reconcile_seed=None)
    result = await execute_seed_first(_spec(), cli_live=True, transport=tx)
    assert result.status == "SEED_SUBMISSION_AMBIGUOUS_STOP"
    assert result.actual_worst_case_pnl is None
    assert len(tx.created) == 1


@pytest.mark.asyncio
async def test_late_reconciled_full_seed_can_continue():
    seed = _state("seed", "late-seed", "LOW", "1", "0", taker_cost="0.01", taker_fee="0.01")
    tx = FailureTransport(seed_mode="ambiguous", maker_mode="reject", reconcile_seed=seed)
    tx.seed = seed
    result = await execute_seed_first(_spec(), cli_live=True, transport=tx)
    assert result.status == "SEED_ONLY_MAKER_REJECTED"
    assert result.actual_worst_case_pnl == D("-0.02")
    assert len(tx.created) == 2


@pytest.mark.asyncio
async def test_known_maker_rejection_leaves_only_bounded_seed():
    tx = FailureTransport(seed_mode="full", maker_mode="reject")
    result = await execute_seed_first(_spec(), cli_live=True, transport=tx)
    assert result.status == "SEED_ONLY_MAKER_REJECTED"
    assert result.actual_worst_case_pnl == D("-0.02")


@pytest.mark.asyncio
async def test_unresolved_maker_ambiguity_returns_without_retrying_order():
    tx = FailureTransport(seed_mode="full", maker_mode="ambiguous", reconcile_maker=None)
    result = await execute_seed_first(_spec(), cli_live=True, transport=tx)
    assert result.status == "MAKER_SUBMISSION_AMBIGUOUS"
    assert result.actual_worst_case_pnl is None
    assert len(tx.created) == 2

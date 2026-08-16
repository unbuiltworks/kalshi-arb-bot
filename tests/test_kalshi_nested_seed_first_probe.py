from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.kalshi_nested_live_probe_guard import NestedLiveProbeSpec
from src.kalshi_nested_probe_transport import OrderAck, OrderState
from src.kalshi_nested_seed_first_probe import (
    actual_nested_worst_case_pnl,
    execute_seed_first,
    modeled_seed_cash_risk,
    validate_seed_first,
)

D = Decimal
NY = ZoneInfo("America/New_York")


def _spec(**overrides):
    values = dict(
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
    values.update(overrides)
    return NestedLiveProbeSpec(**values)


def _state(order_id, client_id, ticker, fill, remaining, *, taker_cost="0", maker_cost="0", taker_fee="0", maker_fee="0"):
    return OrderState(
        order_id=order_id,
        client_order_id=client_id,
        ticker=ticker,
        fill_count=D(fill),
        remaining_count=D(remaining),
        taker_fill_cost=D(taker_cost),
        maker_fill_cost=D(maker_cost),
        taker_fees=D(taker_fee),
        maker_fees=D(maker_fee),
    )


class FakeTransport:
    def __init__(self, seed_state, maker_state=None):
        self.seed_state = seed_state
        self.maker_state = maker_state
        self.created = []
        self.canceled = []

    async def create(self, payload):
        self.created.append(payload)
        if payload["time_in_force"] == "immediate_or_cancel":
            return OrderAck("seed", payload["client_order_id"], self.seed_state.fill_count, self.seed_state.remaining_count, 1)
        return OrderAck("maker", payload["client_order_id"], D("0"), D("1"), 2)

    async def get(self, order_id):
        return self.seed_state if order_id == "seed" else self.maker_state

    async def cancel(self, order_id):
        self.canceled.append(order_id)
        if order_id == "seed":
            self.seed_state = OrderState(**{**self.seed_state.__dict__, "remaining_count": D("0")})
        elif self.maker_state is not None:
            self.maker_state = OrderState(**{**self.maker_state.__dict__, "remaining_count": D("0")})
        return {"order_id": order_id}


@pytest.fixture
def freeze_now(monkeypatch):
    import src.kalshi_nested_live_probe_guard as guard
    class FrozenDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 8, 16, 19, 30, tzinfo=NY)
    monkeypatch.setattr(guard, "datetime", FrozenDateTime)


@pytest.fixture
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr("src.kalshi_nested_seed_first_probe.asyncio.sleep", instant)


def test_current_one_cent_seed_has_two_cent_modeled_cash_risk(freeze_now):
    assert modeled_seed_cash_risk(_spec()) == D("0.02")
    assert validate_seed_first(_spec(), now=datetime(2026, 8, 16, 19, 30, tzinfo=NY)) == D("0.02")


def test_seed_above_two_cent_cash_risk_is_rejected(freeze_now):
    # 2c hedge + 1c taker fee = 3c seed exposure.
    with pytest.raises(ValueError, match="exceeds"):
        validate_seed_first(
            _spec(hedge_cost=D("0.02"), maker_cost=D("0.94")),
            now=datetime(2026, 8, 16, 19, 30, tzinfo=NY),
        )


def test_actual_full_pair_worst_case_is_three_cents():
    seed = _state("s", "sc", "LOW", "1", "0", taker_cost="0.01", taker_fee="0.01")
    maker = _state("m", "mc", "HIGH", "1", "0", maker_cost="0.95")
    assert actual_nested_worst_case_pnl(seed, maker) == D("0.03")


def test_partial_maker_floor_stays_near_seed_risk():
    seed = _state("s", "sc", "LOW", "1", "0", taker_cost="0.01", taker_fee="0.01")
    maker = _state("m", "mc", "HIGH", "0.30", "0", maker_cost="0.285")
    assert actual_nested_worst_case_pnl(seed, maker) == D("-0.005")
    assert actual_nested_worst_case_pnl(seed, None) == D("-0.02")


@pytest.mark.asyncio
async def test_seed_not_full_never_submits_maker(monkeypatch, freeze_now, no_sleep):
    monkeypatch.setattr("src.kalshi_nested_seed_first_probe.live_probe_authorized", lambda *a, **k: True)
    seed = _state("seed", "seed-c", "LOW", "0.50", "0", taker_cost="0.005", taker_fee="0.005")
    tx = FakeTransport(seed)
    result = await execute_seed_first(_spec(), cli_live=True, transport=tx)
    assert result.status == "SEED_NOT_FULL_STOP"
    assert len(tx.created) == 1


@pytest.mark.asyncio
async def test_full_seed_then_full_maker_completes_pair(monkeypatch, freeze_now, no_sleep):
    monkeypatch.setattr("src.kalshi_nested_seed_first_probe.live_probe_authorized", lambda *a, **k: True)
    seed = _state("seed", "seed-c", "LOW", "1", "0", taker_cost="0.01", taker_fee="0.01")
    maker = _state("maker", "maker-c", "HIGH", "1", "0", maker_cost="0.95")
    tx = FakeTransport(seed, maker)
    result = await execute_seed_first(_spec(), cli_live=True, transport=tx)
    assert result.status == "NESTED_PAIR_FULL"
    assert result.actual_worst_case_pnl == D("0.03")
    assert len(tx.created) == 2
    assert tx.created[0]["time_in_force"] == "immediate_or_cancel"
    assert tx.created[1]["post_only"] is True

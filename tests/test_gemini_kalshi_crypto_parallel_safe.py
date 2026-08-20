import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src import gemini_kalshi_crypto_parallel_campaign as p
from src import gemini_kalshi_crypto_parallel_campaign_safe as safe

D = Decimal


def make_state():
    return p.ParallelState("gkp-safe-test", D("5"), {"gemini": D("100"), "kalshi": D("100")})


def test_trade_slot_cap_counts_completed_plus_active(monkeypatch):
    monkeypatch.setattr(p, "MAX_COMPLETED_TRADES", 5)
    state = make_state()
    for asset in p.ASSETS:
        state.lanes[asset].active_run_id = f"run-{asset}"
    assert safe.active_trade_slots(state) == 4
    assert safe.trade_slot_available(state) is True
    state.completed_total = 1
    assert safe.trade_slot_available(state) is False


def test_stop_registered_children_stops_every_process(monkeypatch):
    stopped = []

    async def fake_stop(proc):
        stopped.append(proc)

    monkeypatch.setattr(safe.base, "_stop_child", fake_stop)

    async def scenario():
        registry = {"BTC": object(), "ETH": object(), "SOL": object(), "XRP": object()}
        expected = set(registry.values())
        lock = asyncio.Lock()
        await safe.stop_registered_children(registry, lock)
        assert registry == {}
        assert set(stopped) == expected

    asyncio.run(scenario())


def test_lane_exception_sets_global_halt_and_kills_other_children(monkeypatch):
    monkeypatch.setattr(p, "MAX_COMPLETED_TRADES", 5)
    monkeypatch.setattr(p, "MAX_EXECUTION_ATTEMPTS", 25)
    monkeypatch.setattr(p, "_write_state", lambda state: None)

    async def fail_obtain(*args, **kwargs):
        raise RuntimeError("synthetic unsafe execution")

    stop_calls = []

    async def fake_stop_registered(registry, lock):
        stop_calls.append(True)
        registry.clear()

    monkeypatch.setattr(safe, "_obtain_asset_execution", fail_obtain)
    monkeypatch.setattr(safe, "stop_registered_children", fake_stop_registered)

    async def scenario():
        state = make_state()
        stop_event = asyncio.Event()
        await safe._lane_loop(
            state,
            "BTC",
            datetime.now(timezone.utc) + timedelta(minutes=1),
            None,
            asyncio.Lock(),
            stop_event,
            {"ETH": object()},
            asyncio.Lock(),
        )
        assert state.halted is True
        assert stop_event.is_set() is True
        assert state.lanes["BTC"].status == "HALTED"
        assert stop_calls == [True]

    asyncio.run(scenario())

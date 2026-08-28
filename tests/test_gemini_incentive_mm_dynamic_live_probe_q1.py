import asyncio
import os

import httpx
import pytest

from src import gemini_incentive_mm_dynamic_live_probe_q1 as probe


def test_dynamic_wrapper_dual_key_arming(monkeypatch):
    for name in (probe.LIVE_ENV, probe.PROBE_ID_ENV, probe.PROBE_ARM_ENV):
        monkeypatch.delenv(name, raising=False)
    assert probe.live_armed() is False
    monkeypatch.setenv(probe.LIVE_ENV, "YES")
    monkeypatch.setenv(probe.PROBE_ID_ENV, "abc")
    monkeypatch.setenv(probe.PROBE_ARM_ENV, "wrong")
    assert probe.live_armed() is False
    monkeypatch.setenv(probe.PROBE_ARM_ENV, "abc")
    assert probe.live_armed() is True


def test_dynamic_wrapper_target_and_bounds():
    assert probe.TARGET_ASSET == "ETH"
    assert probe.TARGET_HORIZON == "5m"
    assert probe.dyn.ENTRY_PAIR_EDGE > probe.dyn.FINAL_PAIR_EDGE > 0
    assert probe.dyn.INITIAL_WAIT_SECONDS == 60.0
    assert probe.dyn.RESCUE_SECONDS == 30.0
    assert probe.dyn.MAX_REPRICES == 6
    assert probe.PRE_MUTATION_MAX_ATTEMPTS == 3


@pytest.mark.asyncio
async def test_disarmed_wrapper_makes_no_child_or_local_state_call(monkeypatch):
    for name in (probe.LIVE_ENV, probe.PROBE_ID_ENV, probe.PROBE_ARM_ENV):
        monkeypatch.delenv(name, raising=False)

    async def child_should_not_run():
        raise AssertionError("dynamic child must not run while wrapper is disarmed")

    def local_should_not_run():
        raise AssertionError("local account/open-state must not be touched while disarmed")

    monkeypatch.setattr(probe.dyn, "run", child_should_not_run)
    monkeypatch.setattr(probe.local_base, "local_open_state", local_should_not_run)
    await probe.run()


@pytest.mark.asyncio
async def test_armed_wrapper_restores_discovery_and_child_env(monkeypatch):
    monkeypatch.setenv(probe.LIVE_ENV, "YES")
    monkeypatch.setenv(probe.PROBE_ID_ENV, "wrapper-2")
    monkeypatch.setenv(probe.PROBE_ARM_ENV, "wrapper-2")
    monkeypatch.setattr(probe.local_base, "local_open_state", lambda: None)

    original_discover = probe.base.discover_btc5m
    old_env = {
        probe.dyn.LIVE_ENV: "OLD_LIVE",
        probe.dyn.CANARY_ID_ENV: "OLD_ID",
        probe.dyn.CANARY_ARM_ENV: "OLD_ARM",
    }
    for name, value in old_env.items():
        monkeypatch.setenv(name, value)

    async def child():
        assert probe.base.discover_btc5m is probe.discover_eth5m
        assert os.environ[probe.dyn.LIVE_ENV] == "YES"
        assert os.environ[probe.dyn.CANARY_ID_ENV].startswith("dcq1-")
        assert os.environ[probe.dyn.CANARY_ARM_ENV] == os.environ[probe.dyn.CANARY_ID_ENV]

    monkeypatch.setattr(probe.dyn, "run", child)
    await probe.run()

    assert probe.base.discover_btc5m is original_discover
    for name, value in old_env.items():
        assert os.environ[name] == value


@pytest.mark.asyncio
async def test_live_wrapper_retries_transient_failure_only_with_zero_local_state(monkeypatch):
    calls = 0
    sleeps = []

    async def child():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("temporary terms timeout")

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(probe.dyn, "run", child)
    monkeypatch.setattr(probe.local_base, "local_open_state", lambda: None)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await probe._run_child_with_pre_mutation_retry()
    assert calls == 2
    assert sleeps == [probe.PRE_MUTATION_RETRY_DELAY_S]


@pytest.mark.asyncio
async def test_live_wrapper_never_retries_transport_failure_after_reservation(monkeypatch):
    calls = 0

    async def child():
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout after mutation")

    monkeypatch.setattr(probe.dyn, "run", child)
    monkeypatch.setattr(probe.local_base, "local_open_state", lambda: {"trade_id": "already-reserved"})

    with pytest.raises(httpx.ReadTimeout):
        await probe._run_child_with_pre_mutation_retry()
    assert calls == 1


@pytest.mark.asyncio
async def test_live_wrapper_bounds_pre_mutation_transport_retries(monkeypatch):
    calls = 0

    async def child():
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("still unavailable")

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(probe.dyn, "run", child)
    monkeypatch.setattr(probe.local_base, "local_open_state", lambda: None)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    with pytest.raises(httpx.ReadTimeout):
        await probe._run_child_with_pre_mutation_retry()
    assert calls == probe.PRE_MUTATION_MAX_ATTEMPTS

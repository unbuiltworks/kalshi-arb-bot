from decimal import Decimal as D
import os

import pytest

from src import gemini_incentive_mm_live_probe_q1 as probe


def test_dual_key_arming(monkeypatch):
    monkeypatch.delenv(probe.LIVE_ENV, raising=False)
    monkeypatch.delenv(probe.PROBE_ID_ENV, raising=False)
    monkeypatch.delenv(probe.PROBE_ARM_ENV, raising=False)
    assert not probe.live_armed()

    monkeypatch.setenv(probe.LIVE_ENV, "YES")
    monkeypatch.setenv(probe.PROBE_ID_ENV, "abc")
    monkeypatch.setenv(probe.PROBE_ARM_ENV, "wrong")
    assert not probe.live_armed()

    monkeypatch.setenv(probe.PROBE_ARM_ENV, "abc")
    assert probe.live_armed()


def test_probe_target_is_eth5_only():
    assert probe.TARGET_ASSET == "ETH"
    assert probe.TARGET_HORIZON == "5m"
    assert probe.ETH5_EVENT_RE.match("ETH05M2608281700")
    assert not probe.ETH5_EVENT_RE.match("BTC05M2608281700")
    assert not probe.ETH5_EVENT_RE.match("ETH15M2608281700")


def test_quote_band_gate():
    assert probe.quotes_in_incentive_band({"yes_quote": D("0.20"), "no_quote": D("0.80")})
    assert probe.quotes_in_incentive_band({"yes_quote": D("0.50"), "no_quote": D("0.49")})
    assert not probe.quotes_in_incentive_band({"yes_quote": D("0.19"), "no_quote": D("0.80")})
    assert not probe.quotes_in_incentive_band({"yes_quote": D("0.20"), "no_quote": D("0.81")})


def test_patched_pair_model_poison_outside_incentive_band():
    def original(bid, ask):
        return {
            "yes_quote": D("0.19"),
            "no_quote": D("0.80"),
            "modeled_edge": D("0.05"),
        }

    out = probe.patched_pair_model(original)(D("0.19"), D("0.21"))
    assert out["modeled_edge"] == D("-1")


def test_patched_pair_model_preserves_in_band_edge():
    def original(bid, ask):
        return {
            "yes_quote": D("0.60"),
            "no_quote": D("0.39"),
            "modeled_edge": D("-0.01"),
        }

    out = probe.patched_pair_model(original)(D("0.60"), D("0.61"))
    assert out["modeled_edge"] == D("-0.01")
    assert probe.PROBE_ENTRY_PAIR_EDGE == D("-0.01")


@pytest.mark.asyncio
async def test_disarmed_run_makes_no_child_call(monkeypatch):
    monkeypatch.delenv(probe.LIVE_ENV, raising=False)
    monkeypatch.delenv(probe.PROBE_ID_ENV, raising=False)
    monkeypatch.delenv(probe.PROBE_ARM_ENV, raising=False)

    async def should_not_run():
        raise AssertionError("child must not run while wrapper is disarmed")

    monkeypatch.setattr(probe.inv, "run", should_not_run)
    await probe.run()


@pytest.mark.asyncio
async def test_armed_run_restores_child_env_policy_and_discovery(monkeypatch):
    monkeypatch.setenv(probe.LIVE_ENV, "YES")
    monkeypatch.setenv(probe.PROBE_ID_ENV, "wrapper-1")
    monkeypatch.setenv(probe.PROBE_ARM_ENV, "wrapper-1")
    monkeypatch.setattr(probe.local_base, "local_open_state", lambda: None)

    original_floor = probe.inv.ENTRY_PAIR_EDGE
    original_pair_model = probe.base.pair_model
    original_discover = probe.base.discover_btc5m
    old_env = {
        probe.inv.LIVE_ENV: "OLD_LIVE",
        probe.inv.CANARY_ID_ENV: "OLD_ID",
        probe.inv.CANARY_ARM_ENV: "OLD_ARM",
    }
    for name, value in old_env.items():
        monkeypatch.setenv(name, value)

    async def child():
        assert probe.inv.ENTRY_PAIR_EDGE == D("-0.01")
        assert probe.base.pair_model is not original_pair_model
        assert probe.base.discover_btc5m is probe.discover_eth5m
        assert os.environ[probe.inv.LIVE_ENV] == "YES"
        assert os.environ[probe.inv.CANARY_ID_ENV].startswith("immq1-")
        assert os.environ[probe.inv.CANARY_ARM_ENV] == os.environ[probe.inv.CANARY_ID_ENV]

    monkeypatch.setattr(probe.inv, "run", child)
    await probe.run()

    assert probe.inv.ENTRY_PAIR_EDGE == original_floor
    assert probe.base.pair_model is original_pair_model
    assert probe.base.discover_btc5m is original_discover
    for name, value in old_env.items():
        assert os.environ[name] == value

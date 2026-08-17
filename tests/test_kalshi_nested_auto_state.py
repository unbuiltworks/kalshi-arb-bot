from datetime import datetime, timedelta
from decimal import Decimal
import json

import pytest

from src.kalshi_nested_auto_governor import AutoRiskGovernor, GovernorLimits, ET
from src.kalshi_nested_auto_state import load_governor_state, save_governor_state

D = Decimal


def _now():
    return datetime(2026, 8, 16, 20, 0, tzinfo=ET)


def test_new_state_initializes_et_day(tmp_path):
    path = tmp_path / "state.json"
    governor, status = load_governor_state(path, limits=GovernorLimits(), now=_now())
    assert status == "NEW_STATE"
    assert governor.day_key == "2026-08-16"
    assert governor.triggers_today == 0


def test_atomic_save_and_restore_preserves_dedupe_and_usage(tmp_path):
    path = tmp_path / "state.json"
    limits = GovernorLimits(cooldown_seconds=60)
    g = AutoRiskGovernor(limits)
    g.record_trigger(
        event_ticker="EVT1",
        pair_key="PAIR1",
        seed_cash_risk=D("0.02"),
        now=_now(),
        monotonic_now=100.0,
    )
    save_governor_state(path, g)
    restored, status = load_governor_state(
        path, limits=limits, now=_now() + timedelta(seconds=10)
    )
    assert status == "RESTORED_STATE"
    assert restored.triggers_today == 1
    assert restored.event_counts == {"EVT1": 1}
    assert restored.pair_keys_seen == {"PAIR1"}
    decision = restored.decision(
        event_ticker="EVT2",
        pair_key="PAIR2",
        quantity=D("1"),
        seed_cash_risk=D("0.02"),
        now=_now() + timedelta(seconds=10),
        monotonic_now=1.0,
    )
    assert decision.reason == "COOLDOWN"


def test_corrupt_state_raises_instead_of_resetting(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_governor_state(path, limits=GovernorLimits(), now=_now())


def test_lockout_survives_disk_round_trip(tmp_path):
    path = tmp_path / "state.json"
    limits = GovernorLimits()
    g = AutoRiskGovernor(limits)
    g.lockout("AMBIGUOUS_ORDER_STATE")
    save_governor_state(path, g)
    restored, _ = load_governor_state(path, limits=limits, now=_now())
    assert restored.locked_out is True
    assert restored.lockout_reason == "AMBIGUOUS_ORDER_STATE"

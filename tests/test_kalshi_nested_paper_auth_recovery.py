import asyncio
import json

import pytest

import src.kalshi_nested_paper_auth_recovery as recovery


def _locked_state():
    return {
        "version": 1,
        "day_key": "2026-08-16",
        "triggers_today": 0,
        "seed_cash_risk_today": "0",
        "event_counts": {},
        "pair_keys_seen": [],
        "last_trigger_at": None,
        "locked_out": True,
        "lockout_reason": recovery.EXPECTED_LOCKOUT_REASON,
    }


def _safe_exchange_audit():
    return {
        "safe_to_scan": True,
        "attempt_count": 0,
        "seed_only_exposure_count": 0,
        "lockout_reasons": [],
        "governor": {"locked_out": False},
        "actual_order_placed": False,
        "read_only": True,
        "order_entry": False,
    }


def test_recovery_archives_only_known_zero_attempt_auth_lockout(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_locked_state()), encoding="utf-8")

    async def fake_audit():
        return _safe_exchange_audit()

    monkeypatch.setattr(recovery, "audit", fake_audit)
    row = asyncio.run(recovery.recover_known_auth_lockout(str(state)))

    assert row["status"] == "PAPER_AUTH_LOCKOUT_RECOVERED"
    assert row["read_only"] is True
    assert row["order_entry"] is False
    assert row["actual_order_placed"] is False
    assert not state.exists()
    backups = list(tmp_path.glob("state.json.pre-auth-recovery-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["lockout_reason"] == recovery.EXPECTED_LOCKOUT_REASON


def test_recovery_refuses_unexpected_lockout_without_touching_state(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    payload = _locked_state()
    payload["lockout_reason"] = "something else"
    state.write_text(json.dumps(payload), encoding="utf-8")

    async def should_not_run():
        raise AssertionError("exchange audit must not run for an unexpected local lockout")

    monkeypatch.setattr(recovery, "audit", should_not_run)
    with pytest.raises(RuntimeError, match="unexpected lockout reason"):
        asyncio.run(recovery.recover_known_auth_lockout(str(state)))

    assert state.exists()

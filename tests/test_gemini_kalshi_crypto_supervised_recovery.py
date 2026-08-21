from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from src import gemini_kalshi_crypto_supervised_recovery as recovery


def _state(campaign_id: str) -> dict:
    return {
        "campaign_id": campaign_id,
        "completed_total": 1,
        "execution_attempts_total": 4,
        "halted": False,
        "halt_reason": None,
        "reservations": {},
        "reserved_capital_usd": "0",
        "unsettled_deployed_usd": "0",
        "venue_reserved_usd": {"gemini": "0", "kalshi": "0"},
        "venue_unsettled_usd": {"gemini": "0", "kalshi": "0"},
        "lanes": {
            asset: {
                "status": "IDLE",
                "active_run_id": None,
                "reserved_usd": "0",
            }
            for asset in ("BTC", "ETH", "SOL", "XRP")
        },
    }


def _expired() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")


def test_unresolved_claim_requires_result(tmp_path: Path) -> None:
    claim = tmp_path / "old-btc-01.attempt.json"
    claim.write_text("{}", encoding="utf-8")
    assert recovery._unresolved_claims(tmp_path, "old") == [claim.name]
    (tmp_path / "old-btc-01.result.json").write_text("{}", encoding="utf-8")
    assert recovery._unresolved_claims(tmp_path, "old") == []


def test_orders_rejects_bad_shape() -> None:
    with pytest.raises(RuntimeError):
        recovery._orders([])
    with pytest.raises(RuntimeError):
        recovery._orders({"orders": {}})
    assert recovery._orders({}) == []
    assert recovery._orders({"orders": [{"id": 1}]}) == [{"id": 1}]


def test_active_campaign_does_not_roll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_CANARY_CAMPAIGN_ID", "active")
    monkeypatch.setenv(recovery.DEADLINE_ENV, _future())
    assert recovery.prepare_if_expired() is None


def test_expired_clean_campaign_rolls_only_after_live_reconcile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    campaign_id = "oldclean"
    parent = tmp_path / "parallel"
    claims = tmp_path / "claims"
    parent.mkdir()
    claims.mkdir()
    (parent / f"{campaign_id}.json").write_text(
        json.dumps(_state(campaign_id)), encoding="utf-8"
    )
    monkeypatch.setattr(recovery.p, "STATE_DIR", parent)
    monkeypatch.setattr(recovery.v2, "_state_dir", lambda: claims)
    monkeypatch.setenv("CRYPTO_CANARY_CAMPAIGN_ID", campaign_id)
    monkeypatch.setenv(recovery.DEADLINE_ENV, _expired())

    called = {"value": False}

    async def fake_reconcile() -> dict:
        called["value"] = True
        return {
            "gemini_available_usd": "10",
            "kalshi_available_usd": "10",
            "gemini_resting_orders": 0,
            "kalshi_resting_orders": 0,
        }

    monkeypatch.setattr(recovery, "_live_reconcile", fake_reconcile)
    result = recovery.prepare_if_expired()
    assert called["value"] is True
    assert result is not None
    assert result["prior_campaign_id"] == campaign_id
    assert result["new_campaign_id"] != campaign_id
    assert result["reason"] == "deadline_expired"
    assert recovery.os.environ["CRYPTO_CANARY_CAMPAIGN_ID"] == result["new_campaign_id"]
    assert recovery.os.environ[recovery.DEADLINE_ENV] == result["arm_not_after_utc"]


def test_expired_campaign_with_unresolved_claim_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    campaign_id = "oldclaim"
    parent = tmp_path / "parallel"
    claims = tmp_path / "claims"
    parent.mkdir()
    claims.mkdir()
    (parent / f"{campaign_id}.json").write_text(
        json.dumps(_state(campaign_id)), encoding="utf-8"
    )
    (claims / f"{campaign_id}-btc-01.attempt.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(recovery.p, "STATE_DIR", parent)
    monkeypatch.setattr(recovery.v2, "_state_dir", lambda: claims)
    monkeypatch.setenv("CRYPTO_CANARY_CAMPAIGN_ID", campaign_id)
    monkeypatch.setenv(recovery.DEADLINE_ENV, _expired())

    async def should_not_run() -> dict:
        raise AssertionError("live reconcile must not run with unresolved claim")

    monkeypatch.setattr(recovery, "_live_reconcile", should_not_run)
    with pytest.raises(RuntimeError, match="unresolved persistent attempt claims"):
        recovery.prepare_if_expired()


def test_expired_halted_campaign_fails_before_live_reconcile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    campaign_id = "oldhalt"
    parent = tmp_path / "parallel"
    claims = tmp_path / "claims"
    parent.mkdir()
    claims.mkdir()
    state = _state(campaign_id)
    state["halted"] = True
    state["halt_reason"] = "test"
    (parent / f"{campaign_id}.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(recovery.p, "STATE_DIR", parent)
    monkeypatch.setattr(recovery.v2, "_state_dir", lambda: claims)
    monkeypatch.setenv("CRYPTO_CANARY_CAMPAIGN_ID", campaign_id)
    monkeypatch.setenv(recovery.DEADLINE_ENV, _expired())

    async def should_not_run() -> dict:
        raise AssertionError("live reconcile must not run for halted campaign")

    monkeypatch.setattr(recovery, "_live_reconcile", should_not_run)
    with pytest.raises(RuntimeError, match="not locally rollover-eligible"):
        recovery.prepare_if_expired()

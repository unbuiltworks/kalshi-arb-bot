import asyncio
from pathlib import Path

import pytest

from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_crypto_preclaim_retry_boot as retry


def _paths(tmp_path: Path, run_id: str):
    return (
        tmp_path / f"{run_id}.arm",
        tmp_path / f"{run_id}.attempt.json",
        tmp_path / f"{run_id}.result.json",
    )


def test_synthetic_preclaim_miss_is_existing_benign_shape():
    rec = retry._synthetic_preclaim_miss("r1", "XRP", RuntimeError("boom"))
    assert rec["preclaim_no_order_proof"] is True
    assert reliable._benign_primary_miss(rec["result"]) is True
    assert rec["result"]["gemini"]["filled_quantity"] == "0"
    assert rec["result"]["kalshi"] is None


def test_preclaim_exit_requires_no_claim_and_no_result(monkeypatch, tmp_path):
    run_id = "r2"
    arm, claim, result = _paths(tmp_path, run_id)
    monkeypatch.setattr(retry.v2, "_paths", lambda _: (arm, claim, result))
    exc = RuntimeError(f"asset child exited 1 before result run_id={run_id}")
    assert retry._preclaim_exit(exc, run_id) is True
    claim.write_text("claimed")
    assert retry._preclaim_exit(exc, run_id) is False
    claim.unlink()
    result.write_text("{}")
    assert retry._preclaim_exit(exc, run_id) is False


def test_non_child_runtime_error_is_not_benign(monkeypatch, tmp_path):
    run_id = "r3"
    arm, claim, result = _paths(tmp_path, run_id)
    monkeypatch.setattr(retry.v2, "_paths", lambda _: (arm, claim, result))
    assert retry._preclaim_exit(RuntimeError("some programming bug"), run_id) is False


@pytest.mark.asyncio
async def test_unclaimed_child_exit_becomes_benign_retry(monkeypatch, tmp_path):
    run_id = "r4"
    arm, claim, result = _paths(tmp_path, run_id)
    monkeypatch.setattr(retry.v2, "_paths", lambda _: (arm, claim, result))

    async def fail_before_claim(*args, **kwargs):
        raise RuntimeError(f"asset child exited 1 before result run_id={run_id}")

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(retry, "_ORIGINAL_OBTAIN_ASSET_EXECUTION", fail_before_claim)
    monkeypatch.setattr(retry.asyncio, "sleep", fake_sleep)

    rec = await retry._obtain_asset_execution_with_preclaim_retry(
        run_id, "XRP", None, None, None, None,
    )
    assert reliable._benign_primary_miss(rec["result"]) is True
    assert rec["preclaim_no_order_proof"] is True
    assert sleeps == [retry.RETRY_BACKOFF_S]


@pytest.mark.asyncio
async def test_claimed_child_exit_still_raises(monkeypatch, tmp_path):
    run_id = "r5"
    arm, claim, result = _paths(tmp_path, run_id)
    claim.write_text("claimed")
    monkeypatch.setattr(retry.v2, "_paths", lambda _: (arm, claim, result))

    async def fail_after_claim(*args, **kwargs):
        raise RuntimeError(f"asset child exited 1 before result run_id={run_id}")

    monkeypatch.setattr(retry, "_ORIGINAL_OBTAIN_ASSET_EXECUTION", fail_after_claim)
    with pytest.raises(RuntimeError):
        await retry._obtain_asset_execution_with_preclaim_retry(
            run_id, "XRP", None, None, None, None,
        )


@pytest.mark.asyncio
async def test_existing_benign_primary_miss_gets_cooldown(monkeypatch):
    async def benign(*args, **kwargs):
        return {
            "result": {
                "outcome": "PRIMARY_NOT_FILLED",
                "hard_halt_required": False,
                "gemini": {
                    "filled_quantity": "0",
                    "status": "CANCELED",
                    "reason": "PRE_SEND_TRADING_WS_UNHEALTHY_RETRY_REQUIRED",
                },
                "kalshi": None,
            }
        }

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(retry, "_ORIGINAL_OBTAIN_ASSET_EXECUTION", benign)
    monkeypatch.setattr(retry.asyncio, "sleep", fake_sleep)
    rec = await retry._obtain_asset_execution_with_preclaim_retry(
        "r6", "SOL", None, None, None, None,
    )
    assert reliable._benign_primary_miss(rec["result"])
    assert sleeps == [retry.RETRY_BACKOFF_S]


@pytest.mark.asyncio
async def test_successful_fill_has_no_retry_sleep(monkeypatch):
    async def filled(*args, **kwargs):
        return {"result": {"outcome": "BOTH_FILLED", "hard_halt_required": False}}

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(retry, "_ORIGINAL_OBTAIN_ASSET_EXECUTION", filled)
    monkeypatch.setattr(retry.asyncio, "sleep", fake_sleep)
    rec = await retry._obtain_asset_execution_with_preclaim_retry(
        "r7", "BTC", None, None, None, None,
    )
    assert rec["result"]["outcome"] == "BOTH_FILLED"
    assert sleeps == []

import asyncio
import json

import pytest

from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_preclaim_child_exit_retry as retry


def _dummy_args():
    return ("run-1", "XRP", object(), object(), {}, object())


def test_child_exit_without_claim_or_result_is_benign_retry(tmp_path, monkeypatch):
    arm = tmp_path / "run.arm"
    claim = tmp_path / "run.attempt.json"
    result = tmp_path / "run.result.json"
    monkeypatch.setattr(retry.v2, "_paths", lambda _run_id: (arm, claim, result))

    async def original(*_args):
        raise RuntimeError("asset child exited 1 before result run_id=run-1")

    record = asyncio.run(retry._wrap_obtain(original)(*_dummy_args()))
    assert record["startup_retry"] is True
    assert reliable._benign_primary_miss(record["result"]) is True
    assert record["result"]["gemini"]["filled_quantity"] == "0"
    assert record["result"]["kalshi"] is None
    assert record["result"]["hard_halt_required"] is False


def test_claim_without_result_remains_fail_closed(tmp_path, monkeypatch):
    arm = tmp_path / "run.arm"
    claim = tmp_path / "run.attempt.json"
    result = tmp_path / "run.result.json"
    claim.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(retry.v2, "_paths", lambda _run_id: (arm, claim, result))

    async def original(*_args):
        raise RuntimeError("asset child exited 1 before result run_id=run-1")

    with pytest.raises(RuntimeError, match="asset child exited 1"):
        asyncio.run(retry._wrap_obtain(original)(*_dummy_args()))


def test_result_wins_exit_race(tmp_path, monkeypatch):
    arm = tmp_path / "run.arm"
    claim = tmp_path / "run.attempt.json"
    result = tmp_path / "run.result.json"
    expected = {"run_id": "run-1", "result": {"outcome": "BOTH_FILLED"}}
    result.write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(retry.v2, "_paths", lambda _run_id: (arm, claim, result))

    async def original(*_args):
        raise RuntimeError("asset child exited 1 before result run_id=run-1")

    record = asyncio.run(retry._wrap_obtain(original)(*_dummy_args()))
    assert record == expected


def test_unrelated_runtime_error_is_never_downgraded(tmp_path, monkeypatch):
    arm = tmp_path / "run.arm"
    claim = tmp_path / "run.attempt.json"
    result = tmp_path / "run.result.json"
    monkeypatch.setattr(retry.v2, "_paths", lambda _run_id: (arm, claim, result))

    async def original(*_args):
        raise RuntimeError("unsafe live execution asset=XRP outcome=UNKNOWN")

    with pytest.raises(RuntimeError, match="unsafe live execution"):
        asyncio.run(retry._wrap_obtain(original)(*_dummy_args()))


def test_exit_for_different_run_id_is_never_downgraded(tmp_path, monkeypatch):
    arm = tmp_path / "run.arm"
    claim = tmp_path / "run.attempt.json"
    result = tmp_path / "run.result.json"
    monkeypatch.setattr(retry.v2, "_paths", lambda _run_id: (arm, claim, result))

    async def original(*_args):
        raise RuntimeError("asset child exited 1 before result run_id=other-run")

    with pytest.raises(RuntimeError, match="other-run"):
        asyncio.run(retry._wrap_obtain(original)(*_dummy_args()))

import asyncio
import json

import pytest

from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_crypto_q5_scale_campaign_retry as retry


def test_q5_child_exit_without_claim_or_result_is_benign(tmp_path, monkeypatch):
    arm = tmp_path / "run.arm"
    claim = tmp_path / "run.attempt.json"
    result = tmp_path / "run.result.json"
    monkeypatch.setattr(retry.v2, "_paths", lambda _run_id: (arm, claim, result))

    async def original(_run_id, _deadline):
        raise RuntimeError("q5 child exited 1 before persistent result run_id=q5-01")

    record = asyncio.run(retry._wrap_obtain(original)("q5-01", object()))
    assert record["startup_retry"] is True
    assert reliable._benign_primary_miss(record["result"]) is True
    assert record["result"]["gemini"]["filled_quantity"] == "0"
    assert record["result"]["kalshi"] is None
    assert record["result"]["hard_halt_required"] is False


def test_q5_claim_without_result_remains_fail_closed(tmp_path, monkeypatch):
    arm = tmp_path / "run.arm"
    claim = tmp_path / "run.attempt.json"
    result = tmp_path / "run.result.json"
    claim.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(retry.v2, "_paths", lambda _run_id: (arm, claim, result))

    async def original(_run_id, _deadline):
        raise RuntimeError("q5 child exited 1 before persistent result run_id=q5-01")

    with pytest.raises(RuntimeError, match="q5 child exited 1"):
        asyncio.run(retry._wrap_obtain(original)("q5-01", object()))


def test_q5_result_wins_exit_race(tmp_path, monkeypatch):
    arm = tmp_path / "run.arm"
    claim = tmp_path / "run.attempt.json"
    result = tmp_path / "run.result.json"
    expected = {"run_id": "q5-01", "result": {"outcome": "BOTH_FILLED"}}
    result.write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(retry.v2, "_paths", lambda _run_id: (arm, claim, result))

    async def original(_run_id, _deadline):
        raise RuntimeError("q5 child exited 1 before persistent result run_id=q5-01")

    assert asyncio.run(retry._wrap_obtain(original)("q5-01", object())) == expected


def test_q5_unrelated_error_is_not_downgraded(tmp_path, monkeypatch):
    arm = tmp_path / "run.arm"
    claim = tmp_path / "run.attempt.json"
    result = tmp_path / "run.result.json"
    monkeypatch.setattr(retry.v2, "_paths", lambda _run_id: (arm, claim, result))

    async def original(_run_id, _deadline):
        raise RuntimeError("unsafe q5 execution outcome=UNKNOWN")

    with pytest.raises(RuntimeError, match="unsafe q5 execution"):
        asyncio.run(retry._wrap_obtain(original)("q5-01", object()))

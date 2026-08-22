from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import gemini_kalshi_crypto_benign_deadline as deadline


def _paths(root: Path, run_id: str):
    return (
        root / f"{run_id}.arm.json",
        root / f"{run_id}.attempt.json",
        root / f"{run_id}.result.json",
    )


def test_deadline_without_claim_is_benign(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "gk15m-test-btc-01"
    arm, claim, result = _paths(tmp_path, run_id)
    arm.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(deadline.v2, "_paths", lambda _: (arm, claim, result))

    record = deadline._deadline_record_if_clean(run_id)
    assert record["deadline_expiry_no_claim"] is True
    assert record["order_entry"] is False
    assert record["result"]["outcome"] == "PRIMARY_NOT_FILLED"
    assert record["result"]["gemini"]["filled_quantity"] == "0"
    assert record["result"]["kalshi"] is None


def test_deadline_with_claim_and_no_result_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "gk15m-test-eth-01"
    arm, claim, result = _paths(tmp_path, run_id)
    claim.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(deadline.v2, "_paths", lambda _: (arm, claim, result))

    with pytest.raises(RuntimeError, match="attempt claim but no result"):
        deadline._deadline_record_if_clean(run_id)


def test_deadline_prefers_persisted_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = "gk15m-test-sol-01"
    arm, claim, result = _paths(tmp_path, run_id)
    payload = {"result": {"outcome": "BOTH_FILLED"}}
    result.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(deadline.v2, "_paths", lambda _: (arm, claim, result))

    assert deadline._deadline_record_if_clean(run_id) == payload

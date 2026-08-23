from pathlib import Path

import pytest

from src import gemini_kalshi_crypto_sequential_continuous as seq


def _state(**overrides):
    state = {
        "campaign_id": "old",
        "status": "HALTED_FAIL_CLOSED",
        "halt_reason": "TimeoutError: campaign arm deadline reached before execution for old-01",
        "active_run_id": "old-01",
        "pending_capital_usd": None,
    }
    state.update(overrides)
    return state


def _patch_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    arm = tmp_path / "old-01.arm.json"
    claim = tmp_path / "old-01.attempt.json"
    result = tmp_path / "old-01.result.json"
    monkeypatch.setattr(seq.v2, "_paths", lambda _run_id: (arm, claim, result))
    monkeypatch.setattr(seq.v2, "_state_dir", lambda: tmp_path)
    return arm, claim, result


def test_deadline_halt_without_claim_or_result_is_benign(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    state = _state()
    assert seq._benign_deadline_halt_without_submission(state) is True
    seq._assert_local_terminal("old", state)


def test_deadline_halt_with_attempt_claim_remains_fail_closed(monkeypatch, tmp_path):
    _arm, claim, _result = _patch_paths(monkeypatch, tmp_path)
    claim.write_text("{}", encoding="utf-8")
    state = _state()
    assert seq._benign_deadline_halt_without_submission(state) is False
    with pytest.raises(RuntimeError, match="not safely terminal"):
        seq._assert_local_terminal("old", state)


def test_deadline_halt_with_result_remains_fail_closed(monkeypatch, tmp_path):
    _arm, _claim, result = _patch_paths(monkeypatch, tmp_path)
    result.write_text("{}", encoding="utf-8")
    state = _state()
    assert seq._benign_deadline_halt_without_submission(state) is False
    with pytest.raises(RuntimeError, match="not safely terminal"):
        seq._assert_local_terminal("old", state)


def test_other_halt_reason_is_not_reclassified(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    state = _state(halt_reason="RuntimeError: ambiguous venue submission")
    assert seq._benign_deadline_halt_without_submission(state) is False


def test_pending_capital_blocks_deadline_reclassification(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    state = _state(pending_capital_usd="0.50")
    assert seq._benign_deadline_halt_without_submission(state) is False

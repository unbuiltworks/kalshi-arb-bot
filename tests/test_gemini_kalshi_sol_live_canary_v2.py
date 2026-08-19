from decimal import Decimal
import json
from pathlib import Path

import pytest

from src import gemini_kalshi_sol_live_canary_v2 as canary

D = Decimal


def gate(**overrides):
    params = {
        "delay_ms": 50,
        "readiness_20bp": True,
        "quantity": D("1"),
        "locked_profit": D("0.03"),
        "entry_quote_skew_ms": 10,
        "current_quote_skew_ms": 10,
    }
    params.update(overrides)
    return canary.strict_execution_gate_10bp_50ms(**params)


def test_gate_is_narrow():
    assert gate() is True
    assert gate(delay_ms=25) is False
    assert gate(delay_ms=100) is False
    assert gate(readiness_20bp=False) is False
    assert gate(quantity=D("2")) is False
    assert gate(locked_profit=D("0.0299")) is False
    assert gate(entry_quote_skew_ms=51) is False
    assert gate(current_quote_skew_ms=51) is False


def test_constants_preserve_budget():
    assert canary.CANARY_ASSET == "SOL"
    assert canary.CANARY_MIN_DOUBLE_BOUNDARY_BPS == D("10")
    assert canary.CANARY_EXECUTION_DELAY_MS == 50
    assert canary.CANARY_PLANNED_CAPITAL_LIMIT_USD == D("1")
    assert canary.CANARY_HARD_BUDGET_USD == D("5")


def test_deterministic_client_ids_are_stable_and_distinct():
    first = canary.deterministic_client_ids("run-20260818-a")
    second = canary.deterministic_client_ids("run-20260818-a")
    assert first == second
    assert first[0] != first[1]
    assert first[0].endswith("-g")
    assert first[1].endswith("-k")


def test_run_id_validation():
    assert canary._clean_run_id(" abc DEF_123 ") == "abc-DEF_123"
    with pytest.raises(ValueError):
        canary._clean_run_id("")
    with pytest.raises(ValueError):
        canary._clean_run_id("x" * 33)


def test_live_state_requires_persistent_volume(monkeypatch):
    monkeypatch.delenv(canary.VOLUME_ENV, raising=False)
    with pytest.raises(RuntimeError, match="persistent volume"):
        canary._state_dir()


def test_persistent_claim_consumes_arm_once(tmp_path, monkeypatch):
    monkeypatch.setenv(canary.VOLUME_ENV, str(tmp_path))
    monkeypatch.setenv(canary.RUN_ID_ENV, "testrun")
    arm, claim, result = canary._paths("testrun")
    arm.write_text("testrun\n", encoding="utf-8")

    assert canary._persistent_consume_arm_token(arm) is True
    assert not arm.exists()
    assert claim.exists()
    assert not result.exists()
    payload = json.loads(claim.read_text(encoding="utf-8"))
    assert payload["run_id"] == "testrun"
    assert payload["status"] == "ATTEMPT_CLAIMED_BEFORE_SUBMISSION"

    # A restart/re-entry cannot claim the same run a second time.
    arm.write_text("testrun\n", encoding="utf-8")
    assert canary._persistent_consume_arm_token(arm) is False


def test_build_payloads_use_fixed_run_ids(tmp_path, monkeypatch):
    monkeypatch.setenv(canary.VOLUME_ENV, str(tmp_path))
    monkeypatch.setenv(canary.RUN_ID_ENV, "testrun")

    class Plan:
        direction = "KALSHI_YES_GEMINI_NO"
        gemini_limit = D("0.08")
        kalshi_limit = D("0.83")
        gemini_symbol = "GEMI-SOL15M-TEST-UP"
        kalshi_ticker = "KXSOL15M-TEST"

    gemini, kalshi = canary._build_deterministic_payloads(Plan())
    assert gemini["clientOrderId"] == "solc2-testrun-g"
    assert kalshi["client_order_id"] == "solc2-testrun-k"
    assert gemini["quantity"] == "1"
    assert kalshi["count"] == "1.00"

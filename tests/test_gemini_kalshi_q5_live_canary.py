import json
from decimal import Decimal
from types import SimpleNamespace

from src import gemini_kalshi_q5_live_canary as q5

D = Decimal


def _plan(**overrides):
    data = {
        "quantity": D("5"),
        "entry_capital": D("4.50"),
        "entry_locked_profit": D("0.25"),
        "direction": "KALSHI_YES_GEMINI_NO",
        "gemini_limit": D("0.30"),
        "kalshi_limit": D("0.55"),
        "gemini_symbol": "GEMI-BTC15M-X-UP",
        "kalshi_ticker": "KXBTC15M-X",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_q5_strict_gate_requires_five_and_15c_total_and_50ms_skew():
    assert q5.strict_execution_gate_q5(
        delay_ms=50, readiness_20bp=True, quantity=D("5"), locked_profit=D("0.15"),
        entry_quote_skew_ms=50, current_quote_skew_ms=50,
    )
    assert not q5.strict_execution_gate_q5(
        delay_ms=50, readiness_20bp=True, quantity=D("1"), locked_profit=D("0.15"),
        entry_quote_skew_ms=10, current_quote_skew_ms=10,
    )
    assert not q5.strict_execution_gate_q5(
        delay_ms=50, readiness_20bp=True, quantity=D("5"), locked_profit=D("0.149"),
        entry_quote_skew_ms=10, current_quote_skew_ms=10,
    )
    assert not q5.strict_execution_gate_q5(
        delay_ms=50, readiness_20bp=True, quantity=D("5"), locked_profit=D("0.20"),
        entry_quote_skew_ms=51, current_quote_skew_ms=10,
    )


def test_q5_payload_builder_uses_five_and_deterministic_ids(monkeypatch):
    monkeypatch.setenv(q5.v2.RUN_ID_ENV, "q5-unit-01")
    g, k = q5._build_q5_deterministic_payloads(_plan())
    assert g["quantity"] == "5"
    assert k["count"] == "5.00"
    assert g["clientOrderId"] == "solc2-q5-unit-01-g"
    assert k["client_order_id"] == "solc2-q5-unit-01-k"
    assert g["eventOutcome"] == "NO"
    assert k["side"] == "bid"


def test_q5_payload_builder_rejects_nonfive_or_over_budget(monkeypatch):
    monkeypatch.setenv(q5.v2.RUN_ID_ENV, "q5-unit-02")
    try:
        q5._build_q5_deterministic_payloads(_plan(quantity=D("4")))
        assert False, "expected quantity rejection"
    except ValueError as exc:
        assert "quantity=5" in str(exc)
    try:
        q5._build_q5_deterministic_payloads(_plan(entry_capital=D("5.01")))
        assert False, "expected capital rejection"
    except ValueError as exc:
        assert "exceeds $5" in str(exc)


def test_q5_persistent_claim_records_five_before_submission(monkeypatch, tmp_path):
    monkeypatch.setenv(q5.v2.VOLUME_ENV, str(tmp_path))
    monkeypatch.setenv(q5.v2.RUN_ID_ENV, "q5-claim-01")
    arm, claim, result = q5.v2._paths("q5-claim-01")
    arm.write_text("q5-claim-01\n", encoding="utf-8")
    assert q5._consume_q5_arm(arm) is True
    assert not arm.exists()
    assert claim.exists()
    payload = json.loads(claim.read_text(encoding="utf-8"))
    assert payload["quantity"] == "5"
    assert payload["planned_capital_limit_usd"] == "5"
    assert payload["hard_budget_usd"] == "5"
    assert not result.exists()
    assert q5._consume_q5_arm(arm) is False

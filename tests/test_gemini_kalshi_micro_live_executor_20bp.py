from decimal import Decimal
from types import SimpleNamespace

from src.gemini_kalshi_live_probe_guard import recompute_payload_locked_profit
from src.gemini_kalshi_micro_live_executor_20bp import (
    build_original_limit_payloads,
    strict_execution_gate,
)

D = Decimal


def _plan(direction: str, gemini_limit: str, kalshi_limit: str):
    return SimpleNamespace(
        direction=direction,
        gemini_symbol="GEMI-BTC15M2608181400-UP",
        kalshi_ticker="KXBTC15M-26AUG181000-00",
        gemini_limit=D(gemini_limit),
        kalshi_limit=D(kalshi_limit),
    )


def test_strict_gate_requires_100ms_and_both_skews_at_or_below_50ms():
    common = dict(
        readiness_20bp=True,
        quantity=D("1"),
        locked_profit=D("0.03"),
        entry_quote_skew_ms=50,
        current_quote_skew_ms=50,
    )
    assert strict_execution_gate(delay_ms=100, **common) is True
    assert strict_execution_gate(delay_ms=50, **common) is False
    assert strict_execution_gate(delay_ms=100, **{**common, "entry_quote_skew_ms": 51}) is False
    assert strict_execution_gate(delay_ms=100, **{**common, "current_quote_skew_ms": 51}) is False


def test_strict_gate_requires_one_contract_three_cents_and_20bp_readiness():
    common = dict(
        delay_ms=100,
        readiness_20bp=True,
        quantity=D("1"),
        locked_profit=D("0.03"),
        entry_quote_skew_ms=10,
        current_quote_skew_ms=10,
    )
    assert strict_execution_gate(**common) is True
    assert strict_execution_gate(**{**common, "readiness_20bp": False}) is False
    assert strict_execution_gate(**{**common, "quantity": D("2")}) is False
    assert strict_execution_gate(**{**common, "locked_profit": D("0.0299")}) is False


def test_original_limit_payloads_preserve_kalshi_yes_gemini_no_limits():
    plan = _plan("KALSHI_YES_GEMINI_NO", "0.42", "0.50")
    gemini, kalshi = build_original_limit_payloads(plan)
    assert gemini["eventOutcome"] == "NO"
    assert gemini["price"] == "0.42"
    assert kalshi["side"] == "bid"
    assert kalshi["price"] == "0.50"
    assert gemini["timeInForce"] == "FOK"
    assert kalshi["time_in_force"] == "fill_or_kill"


def test_original_limit_payloads_convert_kalshi_no_cost_back_to_yes_space():
    plan = _plan("GEMINI_YES_KALSHI_NO", "0.40", "0.47")
    gemini, kalshi = build_original_limit_payloads(plan)
    assert gemini["eventOutcome"] == "YES"
    assert gemini["price"] == "0.40"
    assert kalshi["side"] == "ask"
    assert kalshi["price"] == "0.53"


def test_original_limit_payloads_recompute_worst_case_economics():
    plan = _plan("GEMINI_YES_KALSHI_NO", "0.40", "0.47")
    gemini, kalshi = build_original_limit_payloads(plan)
    profit = recompute_payload_locked_profit(
        gemini,
        kalshi,
        kalshi_fee_multiplier=D("1"),
    )
    assert profit > D("0")
    # Payload limits, not later improved quotes, define the worst-case send economics.
    assert gemini["price"] == "0.40"
    assert D("1") - D(kalshi["price"]) == D("0.47")

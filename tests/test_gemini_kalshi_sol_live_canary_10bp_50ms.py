from decimal import Decimal

from src import gemini_kalshi_sol_live_canary_10bp_50ms as canary

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


def test_canary_gate_passes_only_at_50ms():
    assert gate(delay_ms=50) is True
    assert gate(delay_ms=25) is False
    assert gate(delay_ms=100) is False


def test_canary_gate_requires_whole_episode_readiness():
    assert gate(readiness_20bp=False) is False


def test_canary_gate_is_exactly_one_contract():
    assert gate(quantity=D("1")) is True
    assert gate(quantity=D("2")) is False
    assert gate(quantity=D("0.5")) is False


def test_canary_gate_keeps_three_cent_fee_inclusive_profit_floor():
    assert gate(locked_profit=D("0.03")) is True
    assert gate(locked_profit=D("0.029999")) is False


def test_canary_gate_keeps_50ms_entry_and_current_skew_ceiling():
    assert gate(entry_quote_skew_ms=50, current_quote_skew_ms=50) is True
    assert gate(entry_quote_skew_ms=51) is False
    assert gate(current_quote_skew_ms=51) is False
    assert gate(entry_quote_skew_ms=None) is False
    assert gate(current_quote_skew_ms=None) is False


def test_canary_constants_are_narrow_and_budgeted():
    assert canary.CANARY_ASSET == "SOL"
    assert canary.CANARY_MIN_DOUBLE_BOUNDARY_BPS == D("10")
    assert canary.CANARY_EXECUTION_DELAY_MS == 50
    assert canary.CANARY_HARD_BUDGET_USD == D("5")

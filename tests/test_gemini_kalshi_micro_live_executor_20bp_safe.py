from decimal import Decimal
from types import SimpleNamespace

from src import gemini_kalshi_micro_live_executor_20bp as executor
from src import gemini_kalshi_micro_live_executor_20bp_safe as safe
from src.gemini_kalshi_live_probe_guard import recompute_payload_locked_profit

D = Decimal


def test_safe_install_preserves_executor_wrapper_after_basis_install():
    safe.install_safe_executor()
    assert safe.basis._INSTALLED is True
    assert safe.exact.hot.collect_all.__name__ == "collect_all_with_sessions"
    assert safe.exact._probe.__name__ == "probe_with_cleanup"


def test_safe_validator_handles_zero_measured_skew_at_hard_50ms_ceiling():
    safe.install_safe_executor()
    plan = SimpleNamespace(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_symbol="GEMI-BTC15M2608181400-UP",
        kalshi_ticker="KXBTC15M-26AUG181000-00",
        gemini_limit=D("0.40"),
        kalshi_limit=D("0.47"),
    )
    gemini, kalshi = executor.build_original_limit_payloads(plan)
    modeled = recompute_payload_locked_profit(
        gemini,
        kalshi,
        kalshi_fee_multiplier=D("1"),
    )
    # The wrapper ignores an upstream truthiness/sentinel artifact and passes the
    # validator the hard live ceiling, after strict_execution_gate has proven the
    # actual runtime skews are <= 50 ms.
    recomputed = executor.validate_exact_probe_payloads(
        gemini,
        kalshi,
        modeled_locked_profit=modeled,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10**9,
    )
    assert recomputed == modeled

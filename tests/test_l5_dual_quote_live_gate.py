"""Production dual-quote behavior: executable, no venue access or live orders."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "l5_dual_quote.py"


def _module():
    spec = importlib.util.spec_from_file_location("l5_dual_quote_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_does_not_enable_two_placements():
    d = _module()
    with patch.dict("os.environ", {}, clear=True):
        assert not d.enabled()
        assert d.max_placements_per_pass() == 1
        assert not d.replenish_enabled()


def test_on_allows_exactly_two_not_three():
    d = _module()
    with patch.dict("os.environ", {"L5_DUAL_QUOTE": "ON"}, clear=True):
        assert d.enabled()
        assert d.max_placements_per_pass() == 2
        assert not d.replenish_enabled()
        assert d.max_placements_per_pass() <= d.MAX_PLACEMENTS_CEILING


def test_replenishment_requires_both_exact_tokens():
    d = _module()
    with patch.dict("os.environ", {"L5_DUAL_REPLENISH": "ON"}, clear=True):
        assert not d.replenish_enabled()
    with patch.dict("os.environ", {"L5_DUAL_QUOTE": "ON",
                                   "L5_DUAL_REPLENISH": "ON"}, clear=True):
        assert d.replenish_enabled()
    with patch.dict("os.environ", {"L5_DUAL_QUOTE": "on",
                                   "L5_DUAL_REPLENISH": "ON"}, clear=True):
        assert not d.replenish_enabled()


def test_ab_mode_is_window_deterministic_and_fail_closed():
    import datetime
    d = _module()
    dt = datetime.datetime
    utc = datetime.timezone.utc
    with patch.dict("os.environ", {"L5_DUAL_QUOTE": "AB"}, clear=True):
        assert d.max_placements_per_pass(dt(2026, 11, 9, 20, 0, tzinfo=utc)) == 1
        assert d.max_placements_per_pass(dt(2026, 11, 9, 20, 15, tzinfo=utc)) == 2
        assert d.max_placements_per_pass(dt(2026, 11, 9, 20, 30, tzinfo=utc)) == 1
        assert d.max_placements_per_pass(dt(2026, 11, 9, 20, 45, tzinfo=utc)) == 2
        assert d.max_placements_per_pass(None) == 1


def test_dual_quote_never_mutates_safety_limits():
    d = _module()
    required = {"max_total_exposure_usd", "max_cash_deployed_usd",
                "max_live_orders", "watchdog_deadman", "authorization_expiry",
                "exact_order_cancellation", "reconciliation"}
    assert required.issubset(set(d.SAFETY))

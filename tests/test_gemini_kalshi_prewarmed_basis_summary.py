from decimal import Decimal as D
from types import SimpleNamespace

from src import gemini_kalshi_prewarmed_basis_summary as summary


def _reset():
    summary._COUNTS.clear()
    summary._TOTAL_CLOSES = 0


def test_qualifies_requires_whole_episode_gate_and_duration():
    state = SimpleNamespace(all_events_gate_2bp=True, all_events_gate_5bp=False)
    assert summary._qualifies(state, 49, "2bp", 50) is False
    assert summary._qualifies(state, 50, "2bp", 50) is True
    assert summary._qualifies(state, 100, "2bp", 100) is True
    assert summary._qualifies(state, 100, "5bp", 50) is False
    assert summary._qualifies(None, 1000, "2bp", 50) is False


def test_summary_totals_are_additive_and_quote_lock_only():
    _reset()
    summary._record("BTC", "raw_threshold", D("0.59"))
    summary._record("XRP", "raw_threshold", D("0.17"))
    summary._record("XRP", "2bp_50ms", D("0.17"))
    payload = summary.summary_payload()
    assert payload["totals"]["raw_threshold"] == {
        "episodes": 2,
        "apparent_locked_quote_profit": "0.76",
    }
    assert payload["totals"]["2bp_50ms"] == {
        "episodes": 1,
        "apparent_locked_quote_profit": "0.17",
    }
    assert payload["by_asset"]["BTC"]["raw_threshold"]["episodes"] == 1
    assert payload["by_asset"]["XRP"]["2bp_50ms"]["episodes"] == 1
    assert payload["profit_interpretation"] == "APPARENT_QUOTE_LOCK_PROFIT_NOT_REALIZED_PNL"
    assert payload["trade_authorized"] is False

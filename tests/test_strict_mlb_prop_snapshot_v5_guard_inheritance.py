from __future__ import annotations

import inspect

from src import strict_mlb_prop_snapshot_v5 as v5


def test_v5_pricing_inherits_v4_book_and_quote_time_fee_guards() -> None:
    """Participation-aware pricing must not regress behind v4's paper-economics gates."""
    source = inspect.getsource(v5.run_once)

    assert "series/fee_changes" in source
    assert '"show_historical": "true"' in source
    assert "audit_kalshi_binary_book(kalshi_body)" in source
    assert "v4._verified_series_fee(" in source
    assert 'kalshi_book_audit.get("verified") is True' in source
    assert 'fee_audit.get("verified") is True' in source


def test_v5_paper_candidate_filter_requires_verified_book_and_fee_evidence() -> None:
    source = inspect.getsource(v5.run_once)

    assert 'r.get("kalshi_book_integrity_verified") is True' in source
    assert 'r.get("kalshi_fee_verified") is True' in source
    assert '"live_probe_ready_pairs": 0' in source
    assert '"order_entry": False' in source

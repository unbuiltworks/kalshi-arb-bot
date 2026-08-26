from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.kalshi_15m_ev_census import (
    EpisodeTracker,
    _basis_bp,
    _bbo_from_quote,
)


def test_basis_bp_is_official_minus_proxy_relative_basis():
    assert _basis_bp(100.10, 100.00) == pytest.approx(10.0)
    assert _basis_bp(None, 100.0) is None
    assert _basis_bp(100.0, 0.0) is None


def test_two_scale_quote_converts_no_ask_to_yes_bid():
    quote = SimpleNamespace(
        yes_ask=Decimal("0.44"),
        yes_depth=Decimal("12"),
        no_ask=Decimal("0.58"),
        no_depth=Decimal("9"),
        received_ms=123,
    )
    bbo = _bbo_from_quote(quote)
    assert bbo.yes_bid == Decimal("0.42")
    assert bbo.yes_bid_depth == Decimal("9")
    assert bbo.yes_ask == Decimal("0.44")
    assert bbo.yes_ask_depth == Decimal("12")
    assert bbo.no_ask == Decimal("0.58")
    assert bbo.no_ask_depth == Decimal("9")


def test_episode_tracker_opens_only_positive_executable_bucket():
    tracker = EpisodeTracker()
    quote = SimpleNamespace(
        yes_ask=Decimal("0.40"),
        yes_depth=Decimal("20"),
        no_ask=Decimal("0.62"),
        no_depth=Decimal("20"),
        received_ms=0,
    )
    bbo = _bbo_from_quote(quote)
    rows = [
        {
            "q": 1,
            "best_direction": "YES",
            "best_total_ev": "0.05",
            "best_ev_per_contract": "0.05",
        },
        {
            "q": 10,
            "best_direction": None,
            "best_total_ev": None,
            "best_ev_per_contract": None,
        },
    ]
    tracker.update(ticker="TEST", rows=rows, bbo=bbo, now_ns=1_000_000_000)
    assert 1 in tracker.active
    assert 10 not in tracker.active
    assert tracker.active[1].direction == "YES"

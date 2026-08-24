from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src import sports_kp_mve_market_quote_patch as p

D = Decimal


def _base():
    return {
        "ticker": "MKT-X",
        "status": "active",
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    }


def test_yes_ask_derives_from_no_bid_and_depth():
    m = {**_base(), "no_bid_dollars": "0.37", "no_bid_size_fp": "4"}
    out = p.selected_from_market(m, "EVT", "yes", datetime.now(timezone.utc) + timedelta(hours=4))
    assert out is not None
    assert out.ask == D("0.63")
    assert out.depth == D("4")
    assert out.side == "yes"


def test_no_ask_derives_from_yes_bid_and_depth():
    m = {**_base(), "yes_bid_dollars": "0.82", "yes_bid_size_fp": "3"}
    out = p.selected_from_market(m, "EVT", "no", datetime.now(timezone.utc) + timedelta(hours=4))
    assert out is not None
    assert out.ask == D("0.18")
    assert out.depth == D("3")
    assert out.side == "no"


def test_explicit_ask_wins_over_derived_quote():
    m = {
        **_base(),
        "yes_ask_dollars": "0.61",
        "yes_ask_size_fp": "2",
        "no_bid_dollars": "0.44",
        "no_bid_size_fp": "9",
    }
    out = p.selected_from_market(m, "EVT", "yes", datetime.now(timezone.utc) + timedelta(hours=4))
    assert out is not None
    assert out.ask == D("0.61")
    assert out.depth == D("2")


def test_derived_quote_still_requires_q1_depth():
    m = {**_base(), "no_bid_dollars": "0.37", "no_bid_size_fp": "0.5"}
    assert p.selected_from_market(m, "EVT", "yes", datetime.now(timezone.utc) + timedelta(hours=4)) is None


def test_expected_expiration_wins_over_late_administrative_close():
    now = datetime.now(timezone.utc)
    m = {
        "ticker": "MKT-X",
        "status": "active",
        "expected_expiration_time": (now + timedelta(hours=3)).isoformat(),
        "close_time": (now + timedelta(days=3)).isoformat(),
        "yes_ask_dollars": "0.60",
        "yes_ask_size_fp": "2",
    }
    out = p.selected_from_market(m, "EVT", "yes", now + timedelta(hours=24))
    assert out is not None
    assert out.close_time < now + timedelta(hours=24)


def test_expected_expiration_beyond_horizon_rejects_even_if_close_is_earlier():
    now = datetime.now(timezone.utc)
    m = {
        "ticker": "MKT-X",
        "status": "active",
        "expected_expiration_time": (now + timedelta(hours=30)).isoformat(),
        "close_time": (now + timedelta(hours=2)).isoformat(),
        "yes_ask_dollars": "0.60",
        "yes_ask_size_fp": "2",
    }
    assert p.selected_from_market(m, "EVT", "yes", now + timedelta(hours=24)) is None

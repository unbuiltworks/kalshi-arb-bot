from decimal import Decimal

import pytest

from src.gemini_kalshi_live_probe_guard import (
    build_leg_payloads,
    validate_probe_candidate,
)

D = Decimal


def test_kalshi_yes_gemini_no_payloads_are_fok_one_contract():
    gemini, kalshi = build_leg_payloads(
        direction="KALSHI_YES_GEMINI_NO",
        gemini_symbol="GEMI-BTC15M-X-UP",
        kalshi_ticker="KXBTC15M-X",
        gemini_yes_bid=D("0.60"),
        gemini_yes_ask=D("0.61"),
        kalshi_yes_bid=D("0.56"),
        kalshi_yes_ask=D("0.57"),
    )
    assert gemini["eventOutcome"] == "NO"
    assert gemini["price"] == "0.40"
    assert gemini["quantity"] == "1"
    assert gemini["timeInForce"] == "FOK"
    assert kalshi["side"] == "bid"
    assert kalshi["price"] == "0.57"
    assert kalshi["count"] == "1.00"
    assert kalshi["time_in_force"] == "fill_or_kill"


def test_gemini_yes_kalshi_no_uses_yes_ask_and_yes_space_ask():
    gemini, kalshi = build_leg_payloads(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_symbol="GEMI-BTC15M-X-UP",
        kalshi_ticker="KXBTC15M-X",
        gemini_yes_bid=D("0.40"),
        gemini_yes_ask=D("0.41"),
        kalshi_yes_bid=D("0.44"),
        kalshi_yes_ask=D("0.45"),
    )
    assert gemini["eventOutcome"] == "YES"
    assert gemini["price"] == "0.41"
    assert kalshi["side"] == "ask"
    assert kalshi["price"] == "0.44"


def test_probe_guard_rejects_size_edge_or_stale_quotes():
    validate_probe_candidate(quantity=D("1"), locked_profit=D("0.03"), quote_skew_ms=50)
    with pytest.raises(ValueError):
        validate_probe_candidate(quantity=D("2"), locked_profit=D("0.03"), quote_skew_ms=10)
    with pytest.raises(ValueError):
        validate_probe_candidate(quantity=D("1"), locked_profit=D("0.029"), quote_skew_ms=10)
    with pytest.raises(ValueError):
        validate_probe_candidate(quantity=D("1"), locked_profit=D("0.03"), quote_skew_ms=51)

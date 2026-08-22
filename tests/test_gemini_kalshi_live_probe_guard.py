from decimal import Decimal

import pytest

from src.gemini_kalshi_live_probe_guard import (
    SETTLEMENT_EQUIVALENCE_PROVEN,
    build_leg_payloads,
    live_probe_authorized,
    recompute_payload_locked_profit,
    validate_exact_probe_payloads,
    validate_probe_candidate,
)

D = Decimal


def test_incident_lock_blocks_live_even_with_environment_opt_in(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    assert SETTLEMENT_EQUIVALENCE_PROVEN is False
    assert live_probe_authorized(True) is False


def test_incident_lock_blocks_live_without_cli_opt_in(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    assert live_probe_authorized(False) is False


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


def test_exact_payload_guard_recomputes_one_contract_edge_after_fees():
    gemini, kalshi = build_leg_payloads(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_symbol="GEMI-BTC15M-X-UP",
        kalshi_ticker="KXBTC15M-X",
        gemini_yes_bid=D("0.34"),
        gemini_yes_ask=D("0.35"),
        kalshi_yes_bid=D("0.45"),
        kalshi_yes_ask=D("0.46"),
    )
    assert recompute_payload_locked_profit(
        gemini, kalshi, kalshi_fee_multiplier=D("1")
    ) == D("0.06")
    assert validate_exact_probe_payloads(
        gemini,
        kalshi,
        modeled_locked_profit=D("0.06"),
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=20,
    ) == D("0.06")


def test_exact_payload_guard_rejects_stale_modeled_edge():
    gemini, kalshi = build_leg_payloads(
        direction="KALSHI_YES_GEMINI_NO",
        gemini_symbol="GEMI-BTC15M-X-UP",
        kalshi_ticker="KXBTC15M-X",
        gemini_yes_bid=D("0.65"),
        gemini_yes_ask=D("0.66"),
        kalshi_yes_bid=D("0.54"),
        kalshi_yes_ask=D("0.55"),
    )
    assert recompute_payload_locked_profit(
        gemini, kalshi, kalshi_fee_multiplier=D("1")
    ) == D("0.06")
    with pytest.raises(ValueError, match="do not match"):
        validate_exact_probe_payloads(
            gemini,
            kalshi,
            modeled_locked_profit=D("0.05"),
            kalshi_fee_multiplier=D("1"),
            quote_skew_ms=20,
        )


def test_exact_payload_guard_rejects_noncomplementary_legs():
    gemini, kalshi = build_leg_payloads(
        direction="GEMINI_YES_KALSHI_NO",
        gemini_symbol="GEMI-BTC15M-X-UP",
        kalshi_ticker="KXBTC15M-X",
        gemini_yes_bid=D("0.34"),
        gemini_yes_ask=D("0.35"),
        kalshi_yes_bid=D("0.45"),
        kalshi_yes_ask=D("0.46"),
    )
    kalshi = dict(kalshi, side="bid")
    with pytest.raises(ValueError, match="complementary"):
        recompute_payload_locked_profit(gemini, kalshi, kalshi_fee_multiplier=D("1"))

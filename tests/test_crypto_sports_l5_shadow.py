from __future__ import annotations

import time

from src.gemini_maker.crypto_sports_l5_shadow import (
    Market,
    MarketState,
    certify_sports,
)


def _tennis_event():
    return {
        "title": "US Open: Player A vs Player B",
        "subcategory": "Tennis",
        "ticker": "USOPEN-A-B",
        "contracts": [
            {"instrumentSymbol": "USOPEN-A-B-A", "marketState": "open", "title": "Player A to win"},
            {"instrumentSymbol": "USOPEN-A-B-B", "marketState": "open", "title": "Player B to win"},
        ],
    }


def test_certify_simple_two_outcome_tennis_winner():
    row = _tennis_event()
    ok, reason = certify_sports(row, row["contracts"][0], active_contract_count=2)
    assert ok is True
    assert reason == "CERTIFIED_SIMPLE_TWO_OUTCOME_WINNER_V0"


def test_reject_complex_sports_total():
    row = _tennis_event()
    row["title"] = "US Open total games over 22.5"
    ok, reason = certify_sports(row, row["contracts"][0], active_contract_count=2)
    assert ok is False
    assert reason == "COMPLEX_OR_AMBIGUOUS_SPORTS_MARKET"


def _market(category="Crypto", certified=True):
    return Market(
        category=category,
        ticker="BTC05M9999999999" if category == "Crypto" else "SPORT-1",
        symbol="BTC05M9999999999-UP" if category == "Crypto" else "SPORT-1-A",
        title="",
        subcategory="Crypto" if category == "Crypto" else "Tennis",
        expiry_ms=int((time.time() + 3600) * 1000),
        effective_ms=None,
        price_increment=0.01,
        volume24h=100.0,
        certified=certified,
        cert_reason="TEST",
    )


def test_two_sided_proxy_fills_create_matched_inventory():
    state = MarketState(_market())
    t0 = time.monotonic()
    state.on_book(0.40, 0.50, t0)
    rows1 = state.on_trade(price=0.40, qty=2, buyer_is_maker=True, now_mono=t0 + 0.1)
    assert len(rows1) == 1
    assert rows1[0]["side"] == "yes"
    # New BBO snapshot re-arms one q1 quote per side.
    state.on_book(0.40, 0.50, t0 + 0.2)
    rows2 = state.on_trade(price=0.50, qty=2, buyer_is_maker=False, now_mono=t0 + 0.3)
    assert len(rows2) == 1
    assert rows2[0]["side"] == "no"
    summary = state.summary()
    assert summary["matched_qty"] == 1.0
    assert summary["matched_pct"] == 1.0
    assert abs(summary["pair_cost"] - 0.90) < 1e-9
    assert abs(summary["pair_locked"] - 0.10) < 1e-9


def test_uncertified_sports_records_no_proxy_fill():
    state = MarketState(_market(category="Sports", certified=False))
    t0 = time.monotonic()
    state.on_book(0.40, 0.50, t0)
    rows = state.on_trade(price=0.40, qty=10, buyer_is_maker=True, now_mono=t0 + 0.1)
    assert rows == []
    assert state.summary()["fills"] == 0

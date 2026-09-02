"""Exact fixtures for the LIVE documented PMUS WS payload schema
(camelCase, `offers` as the ask book, full trade payloads)."""
from __future__ import annotations

import time

import pytest

from src import pmus_ws_evidence_recorder as rec
from src import pmus_sports_collector as col


LIVE_BOOK = {
    "requestId": "mkt-abc123",
    "subscriptionType": 1,
    "marketData": {
        "marketSlug": "aec-mlb-sea-bos-2026-09-02",
        "bids": [{"px": {"value": "0.4800"}, "qty": "1250.00"},
                 {"px": {"value": "0.4750"}, "qty": "300.00"}],
        "offers": [{"px": {"value": "0.4850"}, "qty": "900.00"}],
        "lastTradePx": {"value": "0.4800"},
    },
}

LIVE_TRADE_SIDED = {
    "requestId": "trd-abc123",
    "subscriptionType": 3,
    "trade": {
        "marketSlug": "aec-mlb-sea-bos-2026-09-02",
        "price": "0.4850",
        "quantity": "12.00",
        "tradeTime": "2026-09-02T21:05:33.123Z",
        "maker": {"orderId": "m1"},
        "taker": {"orderId": "t1", "side": "BUY"},
    },
}

LIVE_TRADE_UNSIDED = {
    "subscriptionType": 3,
    "trade": {
        "marketSlug": "aec-mlb-sea-bos-2026-09-02",
        "price": "0.4800",
        "quantity": "3.00",
        "tradeTime": "2026-09-02T21:06:00.000Z",
        "maker": {"orderId": "m2"},
        "taker": {"orderId": "t2"},
    },
}


def test_live_book_payload_populates_state_with_offers_as_asks():
    st = {}
    rec.apply_events(st, LIVE_BOOK, time.time())
    s = st["aec-mlb-sea-bos-2026-09-02"]
    assert s["bid"] == "0.4800" and s["bid_qty"] == "1250.00"
    assert s["ask"] == "0.4850" and s["ask_qty"] == "900.00"
    assert s["both_sides_served"] is True
    assert s["last_trade_px"] == "0.4800"


def test_live_trade_payload_taker_side_and_bbo_fallback():
    st = {}
    now = time.time()
    rec.apply_events(st, LIVE_BOOK, now)
    rec.apply_events(st, LIVE_TRADE_SIDED, now + 1)
    rec.apply_events(st, LIVE_TRADE_UNSIDED, now + 2)
    trades = st["aec-mlb-sea-bos-2026-09-02"]["trades"]
    assert trades[0]["dir"] == "buy"            # taker.side BUY
    assert trades[0]["qty"] == "12.00"
    assert trades[0]["trade_time"] == "2026-09-02T21:05:33.123Z"
    # unsided trade at the bid -> inferred sell from BBO
    assert trades[1]["dir"] == "sell"


def test_maker_side_flips_when_taker_side_absent():
    ev = {"price": "0.50", "maker": {"side": "BUY"}, "taker": {}}
    assert rec._trade_direction(ev, {}) == "sell"
    ev2 = {"price": "0.50", "taker": {"side": "SELL"}}
    assert rec._trade_direction(ev2, {}) == "sell"
    assert rec._trade_direction({"price": "0.50"}, {}) == "unknown"


def test_status_top_markets_populate_from_live_fixtures():
    st = {}
    now = time.time()
    rec.apply_events(st, LIVE_BOOK, now)
    rec.apply_events(st, LIVE_TRADE_SIDED, now)
    status = rec.build_status(st, True, 10, 40, now)
    top = status["top_markets"][0]
    assert top["slug"] == "aec-mlb-sea-bos-2026-09-02"
    assert top["bid"] == "0.4800" and top["ask"] == "0.4850"
    assert top["both_sides_served"] is True
    assert top["trades_1h"] == 1


def test_snake_case_still_accepted():
    st = {}
    rec.apply_events(st, {"market_data": {
        "market_slug": "aec-y",
        "bids": [{"px": {"value": "0.30"}, "qty": "5"}],
        "asks": [{"px": {"value": "0.31"}, "qty": "6"}],
        "last_trade_px": {"value": "0.30"}}}, time.time())
    assert st["aec-y"]["ask"] == "0.31" and st["aec-y"]["both_sides_served"]


# ---------- permanent config-collision role guard ----------

def test_collector_refuses_to_run_under_recorder_role(monkeypatch):
    monkeypatch.setenv(col.ROLE_ENV, "ws-recorder")
    with pytest.raises(SystemExit) as exc:
        col.assert_service_role()
    assert "SERVICE_ROLE_MISMATCH" in str(exc.value)
    monkeypatch.setenv(col.ROLE_ENV, "collector")
    col.assert_service_role()                    # no raise
    monkeypatch.delenv(col.ROLE_ENV)
    col.assert_service_role()                    # unset stays permissive

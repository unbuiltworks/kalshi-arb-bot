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


LIVE_TRADE_OBJECT_PRICE = {
    "subscriptionType": 3,
    "trade": {
        "marketSlug": "aec-mlb-sea-bos-2026-09-02",
        "price": {"value": "0.4850"},
        "quantity": {"value": "12.00"},
        "tradeTime": "2026-09-02T21:45:00.000Z",
        "maker": {"orderId": "m9"},
        "taker": {"orderId": "t9", "side": "SELL"},
    },
}


def test_object_valued_price_and_quantity_parse_and_gate_survives():
    """Regression for the live crash (issue #167 comment 5516903060):
    trade.price arriving as {value: ...} must normalize, and a malformed
    price must be dropped per-row — build_status can never raise."""
    st = {}
    now = time.time()
    rec.apply_events(st, LIVE_BOOK, now)
    rec.apply_events(st, LIVE_TRADE_OBJECT_PRICE, now)
    s = st["aec-mlb-sea-bos-2026-09-02"]
    assert s["trades"][-1]["px"] == "0.4850"
    assert s["trades"][-1]["dir"] == "sell"
    # malformed price: dropped and counted, never persisted, never raises
    rec.apply_events(st, {"trade": {"marketSlug": "aec-mlb-sea-bos-2026-09-02",
                                    "price": {"weird": True},
                                    "quantity": "1"}}, now)
    assert s.get("bad_trade_rows", 0) == 1
    assert all("weird" not in str(t["px"]) for t in s["trades"])
    status = rec.build_status(st, True, 100, 40, now)   # must not raise
    top = status["top_markets"][0]
    assert top["trades_1h"] >= 1 and "gate_status" in top


def test_multi_interval_state_accumulates_without_reset():
    """History must survive across status intervals (the crash loop was
    resetting it): repeated apply/build cycles keep extending counts."""
    st = {}
    now = time.time()
    rec.apply_events(st, LIVE_BOOK, now)
    for i in range(5):
        rec.apply_events(st, LIVE_TRADE_SIDED, now + i * 60)
        status = rec.build_status(st, True, 10 * (i + 1), 40, now + i * 60)
        assert status["top_markets"][0]["trades_1h"] == i + 1
    assert len(st["aec-mlb-sea-bos-2026-09-02"]["trades"]) == 5


def test_no_quote_microsecond_truncation_regression():
    """Live BAL/COL symptom (#167 comment 5516959618): a fractional epoch
    whose microsecond rounding lands BELOW slot_start made ws_gate's
    synthetic observation fail the >= entry lookup -> NO_QUOTE despite a
    fully served two-sided book. The +1ms guard must yield real slot
    verdicts instead."""
    def book(slug, bid, bq, ask, aq):
        return {"marketData": {"marketSlug": slug,
                               "bids": [{"px": {"value": bid}, "qty": bq}],
                               "offers": [{"px": {"value": ask}, "qty": aq}]}}

    def trade(slug, price, side):
        return {"trade": {"marketSlug": slug, "price": {"value": price},
                          "quantity": "1", "taker": {"side": side}}}

    st = {}
    base = 1_700_000_000.0000004        # rounds DOWN at microsecond precision
    rec.apply_events(st, book("aec-mlb-bal-col", "0.4500", "1001",
                              "0.4550", "4850"), base)
    rec.apply_events(st, trade("aec-mlb-bal-col", "0.4500", "SELL"), base)
    rec.apply_events(st, trade("aec-mlb-bal-col", "0.4700", "BUY"),
                     base + 100)
    g = rec.ws_gate(st["aec-mlb-bal-col"], base + 200)
    assert "NO_QUOTE" not in g["slot_counts_theoretical"], g
    assert g["slot_counts_theoretical"].get("MATCHED", 0) >= 1, g


def test_price_drift_cannot_falsely_clear_the_gate():
    """Regression for #167 comment 5517600205: replaying old slots against
    the CURRENT bid manufactured MATCHED verdicts after a big move. With
    time-aligned snapshots, trades that never crossed the book that
    actually existed at the slot yield no MATCHED."""
    def book(bid, ask):
        return {"marketData": {"marketSlug": "aec-drift",
                               "bids": [{"px": {"value": bid}, "qty": "500"}],
                               "offers": [{"px": {"value": ask}, "qty": "500"}]}}

    def trade(price, side):
        return {"trade": {"marketSlug": "aec-drift",
                          "price": {"value": price}, "quantity": "1",
                          "taker": {"side": side}}}

    st = {}
    base = 1_700_000_000.0
    # book that ACTUALLY existed during the old slot: 0.45/0.455
    rec.apply_events(st, book("0.4500", "0.4550"), base - 10)
    # old-slot trades: a sell at 0.50 (hit resting bids ABOVE our 0.45
    # join) and a buy at 0.70 — under a manufactured 0.65 join these
    # would look like fill + complement; against the real 0.45 book the
    # sell never reaches our join
    rec.apply_events(st, trade("0.5000", "SELL"), base)
    rec.apply_events(st, trade("0.7000", "BUY"), base + 100)
    # price then drifts: CURRENT book is 0.65/0.655
    rec.apply_events(st, book("0.6500", "0.6550"), base + 400)
    g = rec.ws_gate(st["aec-drift"], base + 500)
    assert g["slot_counts_theoretical"].get("MATCHED", 0) == 0, g
    assert g["gate_status"] != "Q1_GATE_CLEARED", g
    # and the honest positive still works: trades that DID cross the
    # time-aligned book produce a MATCHED slot
    st2 = {}
    rec.apply_events(st2, book("0.4500", "0.4550"), base - 10)
    rec.apply_events(st2, trade("0.4500", "SELL"), base)      # fills 0.45
    rec.apply_events(st2, trade("0.5500", "BUY"), base + 100)  # >= 0.46
    g2 = rec.ws_gate(st2["aec-drift"], base + 200)
    assert g2["slot_counts_theoretical"].get("MATCHED", 0) == 1, g2

"""Bounded incremental emission of authenticated trade rows to the log.

The analysis process needs the ROWS, not a summary. Serving them over HTTP
would mean a port, a route and -- to reach anything outside Railway's private
network -- a public domain, which is a new access-control surface for data the
existing log stream already carries. This is the version that adds no surface.

These tests pin the three properties that make it safe and usable: it is OFF
unless asked, it emits each row ONCE so the log is a tape rather than a
repeated snapshot, and it does not touch what the recorder can send.
"""
from __future__ import annotations

import json

from src import pmus_ws_evidence_recorder as R


def _state(rows):
    return {"cpc-btc-updown-15m-x": {"trades": list(rows)}}


ROWS = [{"t": 1.0, "px": "0.22", "qty": 5, "trade_time": "T1", "dir": "buy"},
        {"t": 2.0, "px": "0.23", "qty": 7, "trade_time": "T2", "dir": "sell"}]


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(R.TRADES_LOG_ENV, raising=False)
    R._TRADES_LOG_SEEN.clear()
    assert R.trades_log_cap() == 0
    assert R.emit_new_trades(_state(ROWS)) is None


def test_a_nonnumeric_cap_is_off_not_unbounded(monkeypatch):
    monkeypatch.setenv(R.TRADES_LOG_ENV, "yes")
    assert R.trades_log_cap() == 0


def test_the_cap_is_bounded_from_above(monkeypatch):
    monkeypatch.setenv(R.TRADES_LOG_ENV, "100000")
    assert R.trades_log_cap() == 200


def test_rows_are_emitted_once_so_the_log_is_a_tape(monkeypatch):
    monkeypatch.setenv(R.TRADES_LOG_ENV, "50")
    R._TRADES_LOG_SEEN.clear()
    first = R.emit_new_trades(_state(ROWS))
    assert first and first["rows"] == 2
    assert R.emit_new_trades(_state(ROWS)) is None
    more = ROWS + [{"t": 3.0, "px": "0.24", "qty": 9,
                    "trade_time": "T3", "dir": "unknown"}]
    third = R.emit_new_trades(_state(more))
    assert third and third["rows"] == 1
    assert third["markets"]["cpc-btc-updown-15m-x"][0]["dir"] == "unknown"


def test_the_row_carries_the_fields_the_classifier_needs(monkeypatch):
    monkeypatch.setenv(R.TRADES_LOG_ENV, "50")
    R._TRADES_LOG_SEEN.clear()
    doc = R.emit_new_trades(_state(ROWS))
    row = doc["markets"]["cpc-btc-updown-15m-x"][0]
    assert set(row) == {"t", "px", "qty", "trade_time", "dir"}
    assert "dir=taker direction" in doc["schema"]


def test_it_never_claims_a_fill(monkeypatch):
    monkeypatch.setenv(R.TRADES_LOG_ENV, "50")
    R._TRADES_LOG_SEEN.clear()
    doc = R.emit_new_trades(_state(ROWS))
    assert "Not a fill of ours" in doc["labels"]
    assert "queue position is not implied" in doc["labels"]


def test_the_seen_index_is_bounded(monkeypatch):
    monkeypatch.setenv(R.TRADES_LOG_ENV, "5")
    R._TRADES_LOG_SEEN.clear()
    for i in range(4500):
        R.emit_new_trades(_state([{"t": float(i), "px": "0.1", "qty": 1,
                                   "trade_time": "T", "dir": "buy"}]))
    for slug, seen in R._TRADES_LOG_SEEN.items():
        assert len(seen) <= 4000, slug


def test_the_read_only_outbound_contract_is_untouched():
    assert R.OUTBOUND_FRAME_KEYS == frozenset(
        {"subscribe", "unsubscribe", "heartbeat"})


def test_emission_never_raises_on_a_malformed_state(monkeypatch):
    monkeypatch.setenv(R.TRADES_LOG_ENV, "5")
    R._TRADES_LOG_SEEN.clear()
    assert R.emit_new_trades(None) is None
    assert R.emit_new_trades({"s": None}) is None
    assert R.emit_new_trades({"s": {"trades": None}}) is None

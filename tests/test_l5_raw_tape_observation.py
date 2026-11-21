"""The raw-tape replay must reconstruct the book as it ARRIVED.

It exists because the log-based tape could not: emissions were ~60-110s
apart and carried one state per market, so any latency read off them
measured our own cadence. These tests pin the properties that make the
replay a measurement instead of another cadence artifact.
"""
from __future__ import annotations

import datetime as dt
import json

from scripts import l5_raw_tape_observation as T

WS = dt.datetime(2026, 9, 23, 23, 0, tzinfo=dt.timezone.utc).timestamp()
SLUG = "cpc-btc-updown-15m-2026-09-23-2300z"


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


def book(off, bid="0.40", ask="0.60", bq="100", aq="80"):
    return {"kind": "ws", "t": iso(WS + off), "msg": {"marketData": {
        "marketSlug": SLUG, "bids": [{"px": bid, "qty": bq}],
        "offers": [{"px": ask, "qty": aq}], "bestBid": bid, "bestAsk": ask}}}


def trade(off, px="0.59", qty="5"):
    return {"kind": "ws", "t": iso(WS + off), "msg": {
        "marketSlug": SLUG, "price": px, "qty": qty,
        "tradeTime": iso(WS + off)}}


def run(rows, since=None):
    return T.build(T.replay(rows)["state"], since)


def test_each_frame_keeps_its_own_receipt_time():
    r = run([book(4.2), trade(11.5)])["per_window"][0]
    assert r["first_book_latency_s"] == 4.2
    assert r["first_trade_latency_s"] == 11.5
    assert r["book_observed_before_first_trade"] is True


def test_book_after_the_first_trade_is_still_blind():
    r = run([trade(5.0), book(60.0)])["per_window"][0]
    assert r["verdict"] == "BLIND_AT_OPEN"
    assert run([trade(5.0), book(60.0)])["blindness"]["gate"] == "FAIL"


def test_a_one_sided_quote_is_not_a_usable_book():
    rows = [{"kind": "ws", "t": iso(WS + 3.0), "msg": {"marketData": {
        "marketSlug": SLUG, "bids": [{"px": "0.40", "qty": "10"}],
        "bestBid": "0.40"}}}, trade(11.5)]
    r = run(rows)["per_window"][0]
    assert r["first_book_latency_s"] is None
    assert r["verdict"] == "BLIND_AT_OPEN"


def test_depth_survives_the_replay_on_both_sides():
    st = T.replay([book(4.2, bq="123", aq="456")])["state"]
    b = T.book_states(st[SLUG])[0]
    assert b["bid_qty"] == "123" and b["ask_qty"] == "456"
    assert b["status"] == "OK"


def test_a_truncated_line_is_skipped_not_guessed(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    p = raw / "pmus_ws_20260923_23.jsonl"
    p.write_text(json.dumps(book(4.2)) + "\n" + '{"kind":"ws","t":"bad"\n')
    rows = list(T.tape_rows(tmp_path))
    assert len(rows) == 1


def test_non_btc15m_markets_never_enter_the_report():
    rows = [book(4.2), {"kind": "ws", "t": iso(WS + 1), "msg": {"marketData": {
        "marketSlug": "atc-ncaams-lip-ken", "bids": [{"px": "0.1", "qty": "1"}],
        "offers": [{"px": "0.2", "qty": "1"}]}}}]
    out = run(rows)
    assert out["btc15m_markets_in_tape"] == 1


def test_windows_before_the_cutoff_are_not_pooled():
    old = "cpc-btc-updown-15m-2026-09-23-1600z"
    ws = dt.datetime(2026, 9, 23, 16, 0, tzinfo=dt.timezone.utc).timestamp()
    rows = [{"kind": "ws", "t": iso(ws + 5.0), "msg": {"marketData": {
        "marketSlug": old, "bids": [{"px": "0.4", "qty": "1"}],
        "offers": [{"px": "0.6", "qty": "1"}]}}}]
    out = T.build(T.replay(rows)["state"], "2026-09-23T22:30:00+00:00")
    assert out["per_window"] == []
    assert out["blindness"]["live_windows"] == 0


def test_the_gate_needs_at_least_one_live_window():
    assert run([])["blindness"]["gate"] == "FAIL"

"""Deterministic tests for the keyless PMUS sports forward collector."""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

import pytest

from src import pmus_sports_collector as col


def _ev(live, slugs, score="3-2", period="Top 6th"):
    return {"live": live, "eventState": {"score": score, "period": period},
            "markets": [{"slug": s} for s in slugs]}


def _inc(entries):
    return {"programs": [
        {"marketSlug": slug,
         "timePeriods": [{"status": status}]} for slug, status in entries]}


def test_discovery_selects_live_prefixed_markets_only():
    events = {"mlb": [
        _ev(True, ["aec-mlb-a-b", "tsc-mlb-a-b-8pt5", "a5c-mlb-a-b"]),
        _ev(False, ["aec-mlb-c-d"]),               # not live -> excluded
    ]}
    t = col.discover_targets(events, {}, ["aec-", "a5c-"],
                             include_incentive_markets=False)
    assert [x["slug"] for x in t] == ["aec-mlb-a-b", "a5c-mlb-a-b"]
    assert all(x["why"] == "live_league_market" for x in t)
    assert t[0]["event_state"] == {"live": True, "score": "3-2",
                                   "period": "Top 6th"}


def test_discovery_includes_active_incentive_markets_without_duplicates():
    events = {"mlb": [_ev(True, ["aec-mlb-a-b"])]}
    inc = _inc([("aqc-wta-uso-x", "active"),
                ("aqc-wta-uso-y", "expired"),      # inactive -> excluded
                ("aec-mlb-a-b", "active")])        # dup of league market
    t = col.discover_targets(events, inc, ["aec-"], True)
    slugs = [x["slug"] for x in t]
    assert slugs == ["aec-mlb-a-b", "aqc-wta-uso-x"]
    assert t[1]["why"] == "active_liquidity_program"


def test_plan_cycle_respects_rate_budget_and_market_cap():
    # small: unchanged poll
    p = col.plan_cycle(4, 5.0, 8.0, 24)
    assert p == {"kept_markets": 4, "effective_poll_s": 5.0,
                 "dropped_markets": 0}
    # many markets: poll stretched so 2*kept/eff + 0.5 <= budget
    p2 = col.plan_cycle(30, 5.0, 8.0, 24)
    assert p2["kept_markets"] == 24 and p2["dropped_markets"] == 6
    rate = col.CALLS_PER_MARKET * p2["kept_markets"] / p2["effective_poll_s"]
    assert rate + 0.5 <= 8.0 + 1e-9
    # deterministic
    assert col.plan_cycle(30, 5.0, 8.0, 24) == p2


def test_daily_rotation_and_restart_appends(tmp_path):
    d1 = datetime(2026, 9, 2, 23, 59, tzinfo=timezone.utc)
    d2 = datetime(2026, 9, 3, 0, 1, tzinfo=timezone.utc)
    p1 = col.append_rows(tmp_path, [{"kind": "x"}], now=d1)
    p2 = col.append_rows(tmp_path, [{"kind": "y"}], now=d2)
    assert p1.name == "pmus_20260902.jsonl" and p2.name == "pmus_20260903.jsonl"
    # simulated restart on the same day appends, never truncates
    p1b = col.append_rows(tmp_path, [{"kind": "z"}], now=d1)
    assert p1b == p1
    assert len(p1.read_text().strip().splitlines()) == 2


def test_state_is_restart_safe_and_heartbeat_atomic(tmp_path):
    st = col.load_state(tmp_path)
    st["cycles"] = 7
    col.save_state(tmp_path, st)
    st2 = col.load_state(tmp_path)          # fresh read = restart
    assert st2["cycles"] == 7
    col.heartbeat(tmp_path, markets=3)
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb["mode"] == "READ_ONLY_COLLECTOR" and hb["markets"] == 3
    assert not (tmp_path / "heartbeat.tmp").exists()


def test_incentive_snapshot_parsing():
    inc = _inc([("a", "active"), ("b", "expired"), ("a", "active")])
    assert col.active_incentive_slugs(inc) == ["a"]
    assert col.active_incentive_slugs({}) == []


def test_row_never_fabricates_ask_depth():
    bbo = {"bestBid": {"value": "0.40"}, "bestAsk": {"value": "0.41"},
           "lastTradePx": {"value": "0.405"}, "sharesTraded": "100",
           "openInterest": "50", "state": "MARKET_STATE_OPEN"}
    book_bid_only = {"bids": [{"px": {"value": "0.40"}, "qty": "1000"}],
                     "asks": []}
    row = col.bbo_book_row("aec-x", {"league": "mlb", "why": "w",
                                     "event_state": None}, bbo, book_bid_only)
    assert row["bid_touch_qty"] == "1000"
    assert row["ask_touch_qty"] is None and row["ask_book_missing"] is True
    assert row["source"] == "gateway.polymarket.us"
    both = dict(book_bid_only, asks=[{"px": {"value": "0.41"}, "qty": "5"}])
    del both["asks"]  # legacy fallback covered separately below
    both["offers"] = [{"px": {"value": "0.41"}, "qty": "5"}]
    row2 = col.bbo_book_row("aec-x", {"league": "mlb", "why": "w",
                                      "event_state": None}, bbo, both)
    assert row2["ask_touch_qty"] == "5" and row2["ask_book_missing"] is False


def _meta():
    return {"league": "mlb", "why": "w", "event_state": None}


def test_documented_offers_schema_is_the_canonical_ask_side():
    """Issue #167 comments 5534675078/5534766043: PMUS /v1 book serves
    sell depth under `offers`. The falsely-missing ask side (asks-only
    parsing) zeroed ask_side_served_pct across the live slate — this
    locks the documented live shape."""
    bbo = {"bestBid": {"value": "0.40"}, "bestAsk": {"value": "0.42"}}
    book = {"bids": [{"px": {"value": "0.40"}, "qty": "1000"}],
            "offers": [{"px": {"value": "0.42"}, "qty": "77"},
                       {"px": {"value": "0.44"}, "qty": "900"}]}
    row = col.bbo_book_row("aec-x", _meta(), bbo, book)
    assert row["ask_touch_qty"] == "77"          # offers[0].qty exactly
    assert row["ask_book_missing"] is False


def test_empty_or_absent_offers_stays_missing_fail_closed():
    bbo = {"bestBid": {"value": "0.40"}}
    for book in ({"bids": [], "offers": []},
                 {"bids": [], "offers": None},
                 {"bids": []}):
        row = col.bbo_book_row("aec-x", _meta(), bbo, book)
        assert row["ask_touch_qty"] is None
        assert row["ask_book_missing"] is True


def test_served_offers_key_is_authoritative_over_stale_asks():
    """Never a regression back to asks-only parsing: when the venue
    serves the offers key, it decides the sell side — a stale asks
    field can neither fabricate depth (empty offers) nor be preferred
    (both present)."""
    bbo = {"bestBid": {"value": "0.40"}}
    stale = {"bids": [], "offers": [],
             "asks": [{"px": {"value": "0.41"}, "qty": "5"}]}
    row = col.bbo_book_row("aec-x", _meta(), bbo, stale)
    assert row["ask_touch_qty"] is None and row["ask_book_missing"] is True
    both = {"bids": [], "offers": [{"px": {"value": "0.42"}, "qty": "7"}],
            "asks": [{"px": {"value": "0.41"}, "qty": "5"}]}
    row2 = col.bbo_book_row("aec-x", _meta(), bbo, both)
    assert row2["ask_touch_qty"] == "7" and row2["ask_book_missing"] is False


def test_legacy_asks_only_fixture_still_parses_as_fallback():
    # recorded fixtures predating the schema confirmation carry `asks`
    # with no offers key at all — the fallback keeps them readable
    bbo = {"bestBid": {"value": "0.40"}}
    legacy = {"bids": [], "asks": [{"px": {"value": "0.41"}, "qty": "5"}]}
    row = col.bbo_book_row("aec-x", _meta(), bbo, legacy)
    assert row["ask_touch_qty"] == "5" and row["ask_book_missing"] is False


def test_structurally_read_only_keyless():
    src = inspect.getsource(col)
    # only the public keyless gateway, ever
    import re
    hosts = set(re.findall(r"https://([a-z0-9.-]+)", src))
    assert hosts == {"gateway.polymarket.us"}
    assert "api.polymarket.us" not in src
    # no auth material, no websocket, no mutation verbs on the wire
    for forbidden in ("X-PM-Access-Key", "X-PM-Signature", "websocket",
                      "wss://", '"POST"', "'POST'", "method=",
                      "order.place", "/v1/orders", "preview"):
        assert forbidden not in src, forbidden
    # urllib.request.Request is never given a body
    assert "data=" not in src
    # hardcoded sandbox CA path removed; system TLS default
    assert "/root/.ccr" not in src
    assert "create_default_context()" in src


def test_default_mode_is_collector_only():
    assert col.DEFAULT_ROOT == "/state/pmus-sports"
    src = inspect.getsource(col)
    for trading_word in ("shadow_campaign", "armed", "ARM_ENV", "q=1 order",
                         "place_order"):
        assert trading_word not in src

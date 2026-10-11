"""Tests for the authenticated-recorder PMUS_WS_STATUS layer."""
from __future__ import annotations

import json
import time

from src import pmus_ws_evidence_recorder as rec


def _book_msg(slug, bid, bid_qty, ask, ask_qty):
    return {"request_id": "r1", "subscription_type": 1,
            "market_data_snapshot": {
                "market_slug": slug,
                "bids": [{"px": {"value": bid}, "qty": bid_qty}],
                "asks": [{"px": {"value": ask}, "qty": ask_qty}]}}


def _trade_msg(slug, price, qty, side):
    return {"subscription_type": 3,
            "trade": {"market_slug": slug, "price": price, "qty": qty,
                      "side": side}}


def test_extractor_folds_book_and_trades():
    st = {}
    now = time.time()
    rec.apply_events(st, _book_msg("aec-x", "0.48", "120", "0.50", "80"), now)
    rec.apply_events(st, _trade_msg("aec-x", "0.48", "3", "SELL"), now)
    rec.apply_events(st, _trade_msg("aec-x", "0.50", "1", "BUY"), now + 200)
    s = st["aec-x"]
    assert s["bid"] == "0.48" and s["bid_qty"] == "120"
    assert s["ask"] == "0.50" and s["ask_qty"] == "80"
    assert s["both_sides_served"] is True
    assert [t["dir"] for t in s["trades"]] == ["sell", "buy"]


def test_extractor_tolerates_unknown_shapes():
    st = {}
    rec.apply_events(st, {"heartbeat": {}}, time.time())
    rec.apply_events(st, {"weird": [1, 2, {"nested": "x"}]}, time.time())
    assert st == {}
    # bid-only book never claims both sides served
    rec.apply_events(st, {"market_slug": "aec-y",
                          "bids": [{"px": {"value": "0.40"}, "qty": "5"}],
                          "asks": []}, time.time())
    assert st["aec-y"]["both_sides_served"] is False


def test_ws_gate_statuses_from_authenticated_evidence():
    now = time.time()
    st = {}
    rec.apply_events(st, _book_msg("aec-x", "0.48", "120", "0.50", "80"), now)
    # two-sided real trades but only one matched slot -> SHADOW_CANDIDATE
    rec.apply_events(st, _trade_msg("aec-x", "0.48", "1", "SELL"), now - 200)
    rec.apply_events(st, _trade_msg("aec-x", "0.55", "1", "BUY"), now - 100)
    g = rec.ws_gate(st["aec-x"], now)
    assert g["two_sided_trades"] is True
    assert g["gate_status"] in ("SHADOW_CANDIDATE", "Q1_GATE_CLEARED")
    # no trades -> NOT_READY
    st2 = {}
    rec.apply_events(st2, _book_msg("aec-z", "0.48", "9", "0.50", "9"),
                     now)
    assert rec.ws_gate(st2["aec-z"], now)["gate_status"] == "NOT_READY"


def test_status_line_shape_and_no_secret_material(monkeypatch):
    monkeypatch.setenv(rec.KEY_ENV, "kid-abc123")
    monkeypatch.setenv(rec.SECRET_ENV, "c2VjcmV0LXNlZWQtc2VjcmV0LXNlZWQtMTIzNDU2Nzg=")
    st = {}
    now = time.time()
    rec.apply_events(st, _book_msg("aec-x", "0.48", "120", "0.50", "80"), now)
    line = json.dumps(rec.build_status(st, True, 42, 7))
    assert '"conn": "connected"' in line and '"subscribed_markets": 7' in line
    assert '"messages_received": 42' in line
    assert "THEORETICAL" in line
    # never leaks env values
    assert "kid-abc123" not in line and "c2VjcmV0" not in line
    top = json.loads(line)["top_markets"][0]
    for key in ("slug", "bid", "bid_qty", "ask", "ask_qty",
                "both_sides_served", "gate_status", "trades_1h"):
        assert key in top


def test_all_league_discovery_mode(monkeypatch):
    from src import pmus_sports_collector as col
    calls = []

    def fake_get(path, timeout=15.0):
        calls.append(path)
        if path == "/v2/leagues":
            return [{"slug": "mlb"}, {"slug": "nfl"}, {"slug": "epl"}]
        if path.startswith("/v2/leagues/"):
            return []
        return {}
    monkeypatch.setattr(col, "http_get_json", fake_get)
    monkeypatch.setenv(rec.LEAGUES_ENV, "all")
    monkeypatch.delenv(rec.MARKETS_ENV, raising=False)
    rec.discover_slugs()
    assert "/v2/leagues" in calls
    assert any(c.startswith("/v2/leagues/epl/events") for c in calls), calls
    # every listed league queried, with the 10-event default cap lifted
    assert any("limit=500" in c for c in calls
               if c.startswith("/v2/leagues/epl/events"))

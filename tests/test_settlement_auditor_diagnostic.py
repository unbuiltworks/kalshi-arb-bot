"""OWNER P0 5560183912: a PENDING market is diagnosed against the venue's
PUBLIC status and the exact shape of the account's activity/position
responses -- read-only, throttled, nothing forced or inferred."""
from __future__ import annotations

from src import pmus_settlement_auditor as auditor
from src import pmus_l5_continuous_session as cont

SLUG = "aec-cs2-fokus-nemi-2026-09-06"


class FakeT:
    """Newest-first pages of 20 with eof/nextCursor (the LIVE 15:26Z shape);
    the cursor is accepted only as ?cursor=; any other query -> 401."""
    PAGES = {
        None: {"activities": [{"type": "ACTIVITY_TYPE_TRADE", "createTime": "2026-09-06T12:56:12Z",
                               "trade": {"marketSlug": SLUG, "price": "0.55"}}],
               "eof": False, "nextCursor": "c1"},
        "c1": {"activities": [{"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
                               "createTime": "2026-09-06T12:31:00Z",
                               "positionResolution": {"marketSlug": "aec-atp-facmen-hugdel-2026-09-06",
                                                      "afterPosition": {"realized": "-0.0139"}}},
                              {"type": "ACTIVITY_TYPE_TRADE", "createTime": "2026-09-06T12:30:00Z",
                               "trade": {"marketSlug": "other"}}],
               "eof": False, "nextCursor": "c2"},
        "c2": {"activities": [{"type": "ACTIVITY_TYPE_TRADE", "createTime": "2026-09-06T11:00:00Z",
                               "trade": {"marketSlug": "old"}}],
               "eof": True, "nextCursor": ""},
    }

    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None, base=None, **kw):
        self.calls.append((method, path, base))
        assert method == "GET"
        if path.startswith(f"/v1/markets/{SLUG}/book"):
            return {"marketData": {"state": "MARKET_STATE_EXPIRED", "stats": {
                "settlementPx": {"value": "0.0000", "currency": "USD"},
                "settlementSetTime": "2026-09-06T13:28:53Z",
                "settlementPriceCalculationMethod": "SETTLEMENT_PRICE_CALCULATION_METHOD_EVENT_TIER_1",
                "lastTradeSetTime": "2026-09-06T13:27:14Z"}}}
        if path.startswith("/v1/markets?slug="):
            return {"markets": [{"slug": SLUG, "status": "MARKET_STATUS_RESOLVED",
                                 "closed": True, "active": True,
                                 "updatedAt": "2026-09-06T15:21:54Z"}]}
        if path.startswith("/v1/portfolio/activities"):
            if "?" not in path:
                return self.PAGES[None]
            q = path.split("?", 1)[1]
            if not q.startswith("cursor="):
                raise OSError("HTTP Error 401")
            return self.PAGES[q.split("=", 1)[1]]
        if path.startswith("/v1/portfolio/positions"):
            return {"positions": [], "availablePositions": [
                {"marketSlug": SLUG, "netPosition": "-0.04", "claimable": True}],
                "eof": True, "nextCursor": ""}
        raise AssertionError(path)


def test_public_market_view_reads_resolution_from_the_gateway():
    t = FakeT()
    v = auditor.public_market_view(SLUG, t)
    assert v["state"] == "MARKET_STATE_EXPIRED" and v["status"] == "MARKET_STATUS_RESOLVED"
    assert v["settlement_px"] == "0.0000" and v["settlement_set_time"] == "2026-09-06T13:28:53Z"
    assert v["public_resolved"] is True
    assert all(b == "https://gateway.polymarket.us" for _, _, b in t.calls)


def test_diagnose_pending_walks_the_cursor_and_reads_available_positions():
    t = FakeT()
    auditor._cursor_param["name"] = None
    d = auditor.diagnose_pending(SLUG, t)
    a = d["activities"]
    assert a["top_level_keys"] == ["activities", "eof", "nextCursor"] and a["rows"] == 1
    w = d["activity_walk"]
    assert w["pages"] == 3 and w["ended"] == "eof" and w["cursor_param"] == "cursor"
    assert w["rows"] == 4 and w["types"]["ACTIVITY_TYPE_POSITION_RESOLUTION"] == 1
    assert w["slug_resolution_rows"] == []          # no resolution for cs2 anywhere
    assert w["oldest_time"] == "2026-09-06T11:00:00Z"
    p = d["positions"]
    assert p["rows"] == 0 and p["available_rows"] == 1
    assert p["available_slug_row"]["netPosition"] == "-0.04"
    assert d["public"]["public_resolved"] is True
    # the cursor parameter name is learned once and reused
    assert auditor._cursor_param["name"] == "cursor"


def test_activity_walk_stops_at_the_market_first_fill_and_audit_sees_older_resolutions():
    t = FakeT()
    auditor._cursor_param["name"] = None
    w = auditor.activity_pages(t, since="2026-09-06T12:45:00Z")
    assert w["pages"] == 2 and w["ended"] == "older_than_since"
    # a resolution 20+ activities back is found by the audit (the single
    # page reader could not see it)
    res = auditor.audit_market("aec-atp-facmen-hugdel-2026-09-06", t)
    assert res["status"] == "REALIZED" and res["realized_usd"] == "-0.0139"
    assert res["activity_walk"]["pages"] >= 2
    # cs2: no resolution anywhere, position only under availablePositions
    res2 = auditor.audit_market(SLUG, t)
    assert res2["status"] == "PENDING_SETTLEMENT" and res2["position_net"] == "0"
    assert res2["available_position"]["netPosition"] == "-0.04"


def test_signature_covers_the_path_without_its_query_string():
    from src import pmus_q1_executor as q1base
    assert q1base.sign_path("/v1/portfolio/activities?cursor=abc") == "/v1/portfolio/activities"
    assert q1base.sign_path("/v1/orders") == "/v1/orders"


def test_settle_held_attaches_the_public_view_and_throttles_the_diagnostic(monkeypatch):
    monkeypatch.setattr(auditor, "audit_market", lambda slug, t, walk=None: {
        "market": slug, "settled": False, "status": "PENDING_SETTLEMENT",
        "realized_confirmed": False, "position_net": "0", "resolution": None})
    logged = []
    monkeypatch.setattr(cont, "_log", lambda kind, **kw: logged.append((kind, kw)))
    cont._pending_diag_last.clear()
    t = FakeT()
    out = cont.settle_held(t, SLUG)
    assert out["status"] == "PENDING_SETTLEMENT"
    assert out["public"]["public_resolved"] is True and out["public"]["settlement_px"] == "0.0000"
    assert [k for k, _ in logged] == ["settlement_diagnostic"]
    assert logged[0][1]["activity_walk"]["rows"] == 4
    out2 = cont.settle_held(t, SLUG)                 # inside the throttle window
    assert out2["public"]["public_resolved"] is True
    assert len(logged) == 1


def _cs2_trade(i, realized="0.01", state="TRADE_STATE_CLEARED"):
    return {"type": "ACTIVITY_TYPE_TRADE", "createTime": f"2026-09-06T12:3{i}:00Z",
            "trade": {"marketSlug": SLUG, "id": f"t{i}", "state": state,
                      "price": "0.36", "qtyDecimal": "0.02", "isAggressor": False,
                      "realizedPnl": realized, "side": "BUY", "outcome": "YES"}}


class FlatT(FakeT):
    """cs2 after resolution: public RESOLVED, no position anywhere, every
    trade CLEARED with the venue's realizedPnl, no POSITION_RESOLUTION."""
    def __init__(self, trades=None, positions=None):
        super().__init__()
        self.trades = trades if trades is not None else [_cs2_trade(i) for i in range(4)]
        self.positions = positions or []
        self.PAGES = {None: {"activities": self.trades, "eof": True, "nextCursor": ""}}

    def request(self, method, path, body=None, base=None, **kw):
        if path.startswith("/v1/portfolio/positions"):
            self.calls.append((method, path, base))
            return {"positions": self.positions, "availablePositions": [], "eof": True}
        return super().request(method, path, body=body, base=base, **kw)


def test_flat_account_in_a_publicly_resolved_market_is_settled_from_venue_numbers():
    auditor._cursor_param["name"] = None
    res = auditor.audit_market(SLUG, FlatT())
    assert res["settled"] is True and res["status"] == "REALIZED"
    assert res["settled_by"] == "venue_market_resolved_account_flat"
    assert res["realized_confirmed"] is True and res["realized_usd"] == "0.04"
    assert res["realized_source"].startswith("trade_activities_realizedPnl")
    assert res["resolution"]["public_settlement_px"] == "0.0000"
    assert res["settlement_key"] == "flat_at_resolution:2026-09-06T13:28:53Z"
    assert res["trade_summary"]["trades"][0]["side_fields"] == {"side": "BUY", "outcome": "YES"}


def test_flat_at_resolution_never_fires_with_a_position_an_uncleared_trade_or_an_open_market():
    auditor._cursor_param["name"] = None
    # a position row anywhere -> still pending (the venue will resolve it)
    res = auditor.audit_market(SLUG, FlatT(positions=[{"marketSlug": SLUG, "netPosition": "-0.04"}]))
    assert res["status"] == "PENDING_SETTLEMENT" and res["settled"] is False
    # an uncleared trade -> pending
    trades = [_cs2_trade(i) for i in range(3)] + [_cs2_trade(3, state="TRADE_STATE_PENDING")]
    assert auditor.audit_market(SLUG, FlatT(trades=trades))["status"] == "PENDING_SETTLEMENT"
    # a trade without the venue's realizedPnl -> settled, realized unconfirmed
    trades = [_cs2_trade(i) for i in range(3)] + [_cs2_trade(3, realized=None)]
    res = auditor.audit_market(SLUG, FlatT(trades=trades))
    assert res["settled"] is True and res["status"] == "SETTLED_REALIZED_UNCONFIRMED"
    assert res["realized_confirmed"] is False and res["realized_usd"] is None
    # the public market not resolved -> pending
    t = FlatT()
    real = t.request

    def open_market(method, path, body=None, base=None, **kw):
        if path.startswith(f"/v1/markets/{SLUG}/book"):
            return {"marketData": {"state": "MARKET_STATE_OPEN", "stats": {}}}
        if path.startswith("/v1/markets?slug="):
            return {"markets": [{"slug": SLUG, "status": "MARKET_STATUS_OPEN"}]}
        return real(method, path, body=body, base=base, **kw)
    t.request = open_market
    assert auditor.audit_market(SLUG, t)["status"] == "PENDING_SETTLEMENT"


def test_new_state_trades_without_realized_settle_flat_with_realized_unconfirmed():
    """LIVE 15:46Z: every trade reads TRADE_STATE_NEW and carries no realized
    field; a flat account in a resolved market is SETTLED with realized
    unconfirmed (never fabricated), and the venue-vs-ledger share totals
    are reported."""
    auditor._cursor_param["name"] = None
    trades = []
    for i in range(4):
        tr = _cs2_trade(i, state="TRADE_STATE_NEW")
        del tr["trade"]["realizedPnl"]
        tr["trade"]["passiveExecution"] = {"orderId": f"o{i}", "side": "BUY"}
        tr["trade"]["cost"] = "0.0072"
        trades.append(tr)
    res = auditor.audit_market(SLUG, FlatT(trades=trades))
    assert res["settled"] is True and res["status"] == "SETTLED_REALIZED_UNCONFIRMED"
    assert res["realized_confirmed"] is False and res["realized_usd"] is None
    assert res["trade_summary"]["uncleared"] is False
    assert res["trade_summary"]["trades"][0]["passive"] == {"orderId": "o0", "side": "BUY"}
    assert res["venue_qty_total"] == "0.08" and res["ledger_qty_total"] is None
    assert auditor.venue_qty_total(res["trade_summary"]) == "0.08"

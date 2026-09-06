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
    monkeypatch.setattr(auditor, "audit_market", lambda slug, t: {
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

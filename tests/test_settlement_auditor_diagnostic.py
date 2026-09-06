"""OWNER P0 5560183912: a PENDING market is diagnosed against the venue's
PUBLIC status and the exact shape of the account's activity/position
responses -- read-only, throttled, nothing forced or inferred."""
from __future__ import annotations

from src import pmus_settlement_auditor as auditor
from src import pmus_l5_continuous_session as cont

SLUG = "aec-cs2-fokus-nemi-2026-09-06"


class FakeT:
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
            if "limit=500" in path:
                return {"activities": [
                    {"type": "ACTIVITY_TYPE_TRADE", "createTime": "t1",
                     "trade": {"marketSlug": "other"}},
                    {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION", "createTime": "t2",
                     "positionResolution": {"marketSlug": SLUG, "afterPosition": {"realized": "0.01"}}}],
                    "nextPageToken": "abc"}
            if "type=" in path or "types=" in path or "marketSlug" in path or "market_slug" in path \
                    or "pageSize" in path or "page_size" in path:
                raise OSError("HTTP Error 400")
            return {"activities": [{"type": "ACTIVITY_TYPE_TRADE", "createTime": "t1",
                                    "trade": {"marketSlug": SLUG, "price": "0.36"}}],
                    "nextPageToken": "abc"}
        if path.startswith("/v1/portfolio/positions"):
            return {"positions": [{"marketSlug": "other", "netPosition": "1"}]}
        raise AssertionError(path)


def test_public_market_view_reads_resolution_from_the_gateway():
    t = FakeT()
    v = auditor.public_market_view(SLUG, t)
    assert v["state"] == "MARKET_STATE_EXPIRED" and v["status"] == "MARKET_STATUS_RESOLVED"
    assert v["settlement_px"] == "0.0000" and v["settlement_set_time"] == "2026-09-06T13:28:53Z"
    assert v["public_resolved"] is True
    assert all(b == "https://gateway.polymarket.us" for _, _, b in t.calls)


def test_diagnose_pending_reports_response_shapes_and_query_variants():
    t = FakeT()
    d = auditor.diagnose_pending(SLUG, t)
    a = d["activities"]
    assert a["top_level_keys"] == ["activities", "nextPageToken"]
    assert a["rows"] == 1 and a["types"] == {"ACTIVITY_TYPE_TRADE": 1}
    assert a["for_slug_n"] == 1 and a["for_slug"][0]["type"] == "ACTIVITY_TYPE_TRADE"
    v = d["activity_query_variants"]
    assert v["?limit=500"]["rows"] == 2 and v["?limit=500"]["slug_resolution_rows"] == 1
    assert v["?type=ACTIVITY_TYPE_POSITION_RESOLUTION"].startswith("OSError")
    assert d["positions"]["slug_row"] is None and d["positions"]["rows"] == 1
    assert d["public"]["public_resolved"] is True


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
    assert logged[0][1]["activities"]["rows"] == 1
    out2 = cont.settle_held(t, SLUG)                 # inside the throttle window
    assert out2["public"]["public_resolved"] is True
    assert len(logged) == 1

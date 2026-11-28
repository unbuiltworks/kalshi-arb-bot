"""Request audit 2026-11-28 (analysis/l5/sports/REQUEST_AUDIT_20261128.md).

Each reduction is OFF by default and, when on, must leave fill detection,
cancellation and the fail-closed account read exactly as they are:

  * ACCT_SEEDS_FILL_LIST  the account read's open-order list stands in for
    one fill-poll list read; every proof the list must give is unchanged
  * LIVE_EVENTS_TTL_S     the live events page is reused inside one pass
  * combo maker           shadow poll interval; shadow-only leg book reuse
  * counters              per-route request counts (reporting only)
"""
import importlib
import os
import time
from decimal import Decimal as D

import pytest

from src import pmus_combo_maker as CM
from src import pmus_request_budget as RB
from src import pmus_sports_two_sided as M
from src import pmus_two_sided_ws as WS
from tests.test_pmus_combo_maker import LEGS, RFQ, Api, maker
from tests.test_pmus_sports_fill_list import S1, f, rest  # noqa: F401


# ------------------------------------------------------------- defaults
def test_every_reduction_is_off_in_code():
    for name in ("PMUS_TWO_SIDED_ACCT_SEEDS_FILL_LIST",
                 "PMUS_TWO_SIDED_LIVE_EVENTS_TTL_S",
                 "PMUS_COMBO_SHADOW_POLL_S", "PMUS_COMBO_QUOTE_BOOK_TTL_S"):
        assert os.getenv(name) is None
    m = importlib.reload(M)
    c = importlib.reload(CM)
    assert m.ACCT_SEEDS_FILL_LIST is False and m.LIVE_EVENTS_TTL_S == 0
    assert c.SHADOW_POLL_S == 0 and c.QUOTE_BOOK_TTL_S == 0
    assert c.poll_interval("shadow") == c.POLL_S == c.poll_interval("live")


# ------------------------------------------- account read seeds the list
def acct(v, at=None, drop=()):
    """An account read as Venue.account() returns it: the open orders as
    the venue lists them, stamped just before the request."""
    rows = [{"id": oid, "marketSlug": o["slug"], "state": "ORDER_STATE_NEW",
             "quantity": str(o["qty"]), "cumQuantity": str(o["filled"])}
            for oid, o in v.orders.items() if o["open"] and oid not in drop]
    at = time.monotonic() if at is None else at
    return {"positions": {}, "open_orders": rows,
            "open_orders_read": {"mono": at,
                                 "wall": time.time() - (time.monotonic()
                                                        - at)}}


def test_off_the_account_read_seeds_nothing(f):
    e, v = f
    rest(e, v)
    v.account = lambda: acct(v)
    assert e.read_account() is not None
    assert getattr(e, "_oo", None) is None
    e.step_market(S1)
    assert v.lists == 1 and v.exact == []


def test_on_the_account_list_stands_in_for_one_list_read(f, monkeypatch):
    e, v = f
    monkeypatch.setattr(M, "ACCT_SEEDS_FILL_LIST", True)
    oids = rest(e, v)
    v.account = lambda: acct(v)
    assert e.read_account() is not None
    for _ in range(3):
        e.step_market(S1)
    assert v.lists == 0 and v.exact == []        # no list read, no polls
    assert e._fill_stats()["list_seeded"] == 1
    assert e._fill_stats()["skipped"] == 3 * len(oids)


def test_on_a_seeded_list_expires_like_any_other(f, monkeypatch):
    e, v = f
    monkeypatch.setattr(M, "ACCT_SEEDS_FILL_LIST", True)
    rest(e, v)
    v.account = lambda: acct(v, at=time.monotonic()
                             - M.FILL_LIST_REFRESH_S - 0.5)
    e.read_account()
    e.step_market(S1)
    assert v.lists == 1                 # older than a refresh: read again


def test_on_a_fill_is_still_found_at_once_and_only_by_exact_read(
        f, monkeypatch):
    e, v = f
    monkeypatch.setattr(M, "ACCT_SEEDS_FILL_LIST", True)
    oids = rest(e, v)
    hit = oids[0]
    side = e.b.m(S1)["orders"][hit]["side"]
    v.orders[hit]["filled"], v.orders[hit]["open"] = D("1"), False
    v.account = lambda: acct(v)         # the venue no longer lists it
    e.read_account()
    assert D(e.b.m(S1)["pos"][side]) == 0        # the list books nothing
    e.step_market(S1)
    assert hit in v.exact
    assert D(e.b.m(S1)["pos"][side]) == D("1")


def test_on_a_row_that_disagrees_is_read_exactly(f, monkeypatch):
    e, v = f
    monkeypatch.setattr(M, "ACCT_SEEDS_FILL_LIST", True)
    oids = rest(e, v)
    a = acct(v)
    a["open_orders"][0]["cumQuantity"] = "0.5"   # a partial the book lacks
    v.account = lambda: a
    e.read_account()
    e.step_market(S1)
    assert a["open_orders"][0]["id"] in v.exact and len(oids) == 2


def test_on_an_order_placed_after_the_account_read_is_read_exactly(
        f, monkeypatch):
    e, v = f
    monkeypatch.setattr(M, "ACCT_SEEDS_FILL_LIST", True)
    oids = rest(e, v)
    v.account = lambda: acct(v)
    e.read_account()
    for o in e.b.m(S1)["orders"].values():       # placed after the read
        o["placed_at"] = time.time() + 1
    e.step_market(S1)
    assert sorted(v.exact) == sorted(oids)


def test_on_the_60s_exact_read_is_still_made(f, monkeypatch):
    e, v = f
    monkeypatch.setattr(M, "ACCT_SEEDS_FILL_LIST", True)
    oids = rest(e, v)
    v.account = lambda: acct(v)
    e.read_account()
    e._exact_at = {k: t - M.FILL_EXACT_EVERY_S - 1
                   for k, t in e._exact_at.items()}
    e.step_market(S1)
    assert sorted(v.exact) == sorted(oids)


def test_on_a_failed_account_read_seeds_nothing_and_stays_fail_closed(
        f, monkeypatch):
    e, v = f
    monkeypatch.setattr(M, "ACCT_SEEDS_FILL_LIST", True)
    rest(e, v)

    def boom():
        raise RuntimeError("HTTP Error 429: Too Many Requests")
    v.account = boom
    assert e.read_account() is None and e._acct_fail == 1
    assert getattr(e, "_oo", None) is None
    e.step_market(S1)
    assert v.lists == 1                 # the fill poll reads its own list


def test_on_an_account_read_without_a_stamp_seeds_nothing(f, monkeypatch):
    e, v = f
    monkeypatch.setattr(M, "ACCT_SEEDS_FILL_LIST", True)
    rest(e, v)
    a = acct(v)
    a.pop("open_orders_read")           # the list was not readable as one
    v.account = lambda: a
    e.read_account()
    assert getattr(e, "_oo", None) is None


def test_on_an_older_account_list_never_replaces_a_newer_fill_list(
        f, monkeypatch):
    e, v = f
    monkeypatch.setattr(M, "ACCT_SEEDS_FILL_LIST", True)
    rest(e, v)
    e.step_market(S1)                   # the fill poll reads its own list
    held = e._oo
    v.account = lambda: acct(v, at=held["t"] - 1.0)
    e.read_account()
    assert e._oo is held


def test_on_without_the_fill_poll_nothing_is_seeded(f, monkeypatch):
    e, v = f
    monkeypatch.setattr(M, "ACCT_SEEDS_FILL_LIST", True)
    oids = rest(e, v)
    monkeypatch.setattr(M, "FILL_LIST_POLL", False)
    v.account = lambda: acct(v)
    e.read_account()
    e.step_market(S1)
    assert sorted(v.exact) == sorted(oids)       # every order, every pass


class _T:
    """Signed transport double: answers the two account routes."""

    def __init__(self, orders):
        self.calls, self.orders = [], orders

    def request(self, method, path, *a, **kw):
        self.calls.append((method, path, kw))
        if path == "/v1/portfolio/positions":
            return {"positions": []}
        return self.orders


def _venue(t):
    v = M.Venue.__new__(M.Venue)
    from src import pmus_q1_executor_v4 as q1
    v.q1, v.t = q1, t
    return v


def test_the_account_read_makes_the_same_two_requests_and_stamps_the_list():
    t = _T({"orders": [{"id": "A"}]})
    m0, w0 = time.monotonic(), time.time()
    a = _venue(t).account()
    assert [(m, p) for m, p, _k in t.calls] == [
        ("GET", "/v1/portfolio/positions"), ("GET", "/v1/orders/open")]
    assert all(k == {} for _m, _p, k in t.calls)   # class unchanged: normal
    assert a["open_orders"] == [{"id": "A"}]
    assert m0 <= a["open_orders_read"]["mono"] <= time.monotonic()
    assert w0 <= a["open_orders_read"]["wall"] <= time.time()


def test_an_unreadable_order_list_is_not_stamped():
    a = _venue(_T({"orders": None})).account()
    assert a["open_orders"] == [] and "open_orders_read" not in a


# ------------------------------------------------- live events page reuse
class _LiveVenue(M.Venue):
    def __init__(self):                 # no transport, no network
        self.reads = 0
        self.fail = False

    def _live_sports_read(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError("gateway down")
        return [{"slug": "aec-x"}]


def test_live_events_are_read_every_call_by_default():
    v = _LiveVenue()
    v.live_sports(), v.live_sports()
    assert v.reads == 2


def test_live_events_are_reused_inside_the_ttl(monkeypatch):
    monkeypatch.setattr(M, "LIVE_EVENTS_TTL_S", 5.0)
    v = _LiveVenue()
    a = v.live_sports()
    a.append("mutated by the caller")
    assert v.live_sports() == [{"slug": "aec-x"}] and v.reads == 1
    v._live_cache = (time.monotonic() - 6, v._live_cache[1])
    v.live_sports()
    assert v.reads == 2


def test_a_failed_live_read_is_never_cached(monkeypatch):
    monkeypatch.setattr(M, "LIVE_EVENTS_TTL_S", 5.0)
    v = _LiveVenue()
    v.fail = True
    with pytest.raises(RuntimeError):
        v.live_sports()
    v.fail = False
    assert v.live_sports() == [{"slug": "aec-x"}] and v.reads == 2


# ------------------------------------------------------------ combo maker
def test_shadow_poll_interval_applies_to_shadow_only(monkeypatch):
    monkeypatch.setattr(CM, "POLL_S", 10.0)
    monkeypatch.setattr(CM, "SHADOW_POLL_S", 60.0)
    assert CM.poll_interval("shadow") == 60.0
    assert CM.poll_interval("live") == 10.0
    monkeypatch.setattr(CM, "SHADOW_POLL_S", 2.0)    # never faster
    assert CM.poll_interval("shadow") == 10.0


class CountApi(Api):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.book_reads = []

    def books(self, syms):
        self.book_reads.append(list(syms))
        return super().books(syms)


def _rfqs(n):
    return [dict(RFQ, id="rfq%d" % i) for i in range(n)]


def test_by_default_every_leg_of_every_rfq_is_read():
    api = CountApi(rfqs=_rfqs(3))
    m, _t = maker(api, mode="shadow")
    m.pass_once()
    assert sum(len(x) for x in api.book_reads) == 6
    assert m.status()["leg_book_reads"] == {"read": 6, "reused": 0}


def test_shadow_reuses_a_leg_book_inside_the_ttl(monkeypatch):
    monkeypatch.setattr(CM, "QUOTE_BOOK_TTL_S", 5.0)
    api = CountApi(rfqs=_rfqs(3))
    m, t = maker(api, mode="shadow")
    st = m.pass_once()
    assert st["shadow"] == 3                      # all three still priced
    assert sum(len(x) for x in api.book_reads) == 2
    assert m.leg_reads == {"read": 2, "reused": 4}
    t["now"] += 6                                 # past the TTL: read again
    api.rfqs = _rfqs(4)[3:]
    m.pass_once()
    assert sum(len(x) for x in api.book_reads) == 4


def test_live_never_prices_a_quote_from_a_reused_book(monkeypatch):
    monkeypatch.setattr(CM, "QUOTE_BOOK_TTL_S", 5.0)
    api = CountApi(rfqs=_rfqs(3))
    m, _t = maker(api, mode="live")
    st = m.pass_once()
    assert st["quoted"] == 3
    assert sum(len(x) for x in api.book_reads) == 6


def test_an_unreadable_leg_is_not_cached(monkeypatch):
    monkeypatch.setattr(CM, "QUOTE_BOOK_TTL_S", 5.0)
    api = CountApi(rfqs=_rfqs(2), books={LEGS[0]["symbol"]: None,
                                         LEGS[1]["symbol"]:
                                         {"bid": "0.60", "ask": "0.62"}})
    m, _t = maker(api, mode="shadow")
    m.pass_once()
    assert api.book_reads == [[l["symbol"] for l in LEGS],
                              [LEGS[0]["symbol"]]]


def test_the_last_look_always_reads_fresh_books(monkeypatch):
    monkeypatch.setattr(CM, "QUOTE_BOOK_TTL_S", 3600.0)
    api = CountApi()
    m, _t = maker(api, mode="shadow")
    m._quote_books([l["symbol"] for l in LEGS])   # cache is warm
    api.book_reads.clear()
    m.s["active"]["q1"] = {"rfq_id": "rfq1", "symbol": "caoc-x",
                           "legs": LEGS, "fair": "0.3111", "buy": "0.36",
                           "sell": "0.26", "t": m.clock()}
    api.accepted = [{"id": "q1", "acceptedSide": "SIDE_BUY",
                     "buyQtyDecimal": "2"}]
    m.pass_once()
    assert api.book_reads[0] == [l["symbol"] for l in LEGS]


# --------------------------------------------------------------- counters
def test_route_family_drops_ids_slugs_and_queries():
    rf = RB.route_family
    assert rf("get", "/v1/order/ABC123") == "GET /v1/order/:id"
    assert rf("POST", "/v1/order/ABC123/cancel") == \
        "POST /v1/order/:id/cancel"
    assert rf("POST", "/v1/order/preview") == "POST /v1/order/preview"
    assert rf("GET", "/v1/markets/aec-x/bbo") == "GET /v1/markets/:slug/bbo"
    assert rf("GET", "/v1/market/slug/aec-x") == "GET /v1/market/slug/:slug"
    assert rf("GET", "/v1/portfolio/positions?cursor=zz") == \
        "GET /v1/portfolio/positions"
    assert rf("DELETE", "/v1/rfqs/r1/quotes/q1") == \
        "DELETE /v1/rfqs/:id/quotes/:id"


class _Inner:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def request(self, method, path, *a, **kw):
        self.calls.append((method, path))
        if self.fail:
            raise self.fail
        return {}


class _C:
    t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_signed_requests_are_counted_per_route_and_class_is_unchanged():
    c = _C()
    b = RB.RequestBudget(clock=c, sleep=c.sleep)
    t = RB.GovernedTransport(_Inner(), b)
    t.request("GET", "/v1/order/A")
    t.request("GET", "/v1/order/B")
    t.request("GET", "/v1/orders/open")
    r = b.report()
    assert r["by_route"] == {"GET /v1/order/:id": 2, "GET /v1/orders/open": 1}
    assert r["sent"]["critical"] == 2 and r["sent"]["normal"] == 1


def test_a_deferred_request_is_counted_as_deferred_not_sent():
    c = _C()
    b = RB.RequestBudget(rps=0, burst=0, normal_wait_s=0, clock=c,
                         sleep=c.sleep)
    inner = _Inner()
    t = RB.GovernedTransport(inner, b)
    with pytest.raises(RB.BudgetDeferred):
        t.request("GET", "/v1/rfqs?status=RFQ_STATUS_OPEN&limit=100")
    t.request("POST", "/v1/order/A/cancel", {})   # critical: always sent
    r = b.report()
    assert r["deferred_by_route"] == {"GET /v1/rfqs": 1}
    assert r["by_route"] == {"POST /v1/order/:id/cancel": 1}
    assert inner.calls == [("POST", "/v1/order/A/cancel")]


def test_a_429_is_counted_against_its_route():
    c = _C()
    b = RB.RequestBudget(clock=c, sleep=c.sleep)
    t = RB.GovernedTransport(_Inner(RuntimeError("HTTP Error 429")), b)
    with pytest.raises(RuntimeError):
        t.request("GET", "/v1/portfolio/positions")
    assert b.report()["http_429_by_route"] == \
        {"GET /v1/portfolio/positions": 1}
    assert b.cooling()


def test_the_route_table_is_bounded():
    tbl = {}
    for i in range(RB.ROUTE_FAMILIES_MAX + 50):
        RB.count_route(tbl, "GET /x/%d" % i)
    assert len(tbl) == RB.ROUTE_FAMILIES_MAX + 1 and tbl["other"] == 50


def test_gateway_reads_are_counted_per_route_and_errors_still_raise():
    v = M.Venue.__new__(M.Venue)

    class GW:
        n = 0

        def http_get_json(self, path):
            GW.n += 1
            if "boom" in path:
                err = RuntimeError("HTTP Error 429")
                err.code = 429
                raise err
            return {"ok": path}
    v.gw = GW()
    assert v.gateway_report() == {"sent": 0, "by_route": {}}
    assert v.gget("/v1/markets/aec-a/bbo") == {"ok": "/v1/markets/aec-a/bbo"}
    v.gget("/v1/markets/aec-b/bbo")
    with pytest.raises(RuntimeError):
        v.gget("/v1/market/slug/boom")
    r = v.gateway_report()
    assert r["sent"] == 3 and GW.n == 3
    assert r["by_route"] == {"GET /v1/markets/:slug/bbo": 2,
                             "GET /v1/market/slug/:slug": 1}
    assert r["http_429_by_route"] == {"GET /v1/market/slug/:slug": 1}


def test_ws_fallbacks_are_split_by_cause_and_the_answer_is_unchanged():
    t = {"now": 100.0}
    ws = WS.TwoSidedWs(clock=lambda: t["now"], fresh_s=3.0,
                       log=lambda *a, **k: None)
    assert ws.fresh_bbo("never-seen") is None
    ws.conn_id, ws.connected = 1, True
    ws._top["a"] = {"bid": D("0.4"), "ask": D("0.5"), "t": 100.0, "conn": 1}
    ws.last_frame_t = 100.0
    assert ws.fresh_bbo("a") == {"bid": D("0.4"), "ask": D("0.5")}
    t["now"] = 110.0                              # socket quiet
    assert ws.fresh_bbo("a") is None
    ws.conn_id, ws.last_frame_t = 2, 110.0        # reconnected, not resent
    assert ws.fresh_bbo("a") is None
    ws._top["b"] = {"bid": D("0.4"), "ask": None, "t": 110.0, "conn": 2}
    assert ws.fresh_bbo("b") is None
    s = ws.status()
    assert s["gateway_fallbacks"] == 4 and s["ws_bbo_hits"] == 1
    assert s["gateway_fallbacks_by_cause"] == {
        "no_book": 1, "stale": 1, "other_connection": 1, "not_two_sided": 1}

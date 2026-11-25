"""Fill detection from one open-order list read instead of one status
read per order.

Measured live 2026-11-25 (4 markets, about 6 resting orders): about 70% of
all signed requests were GET /v1/order/{id}, one per open order per pass
(2,669 in 70 minutes); the bot ran at 0.8 of its one request a second and
deferred 26 order sends in 97 minutes. At 8 markets those polls alone
exceed the budget.

Owner 2026-11-25: "Eliminate redundant requests and unnecessary order
replacements. Support substantially more concurrent markets within the
existing venue request budget." Fill detection must stay as fast and as
certain as it is: a fill is recorded only from the order's own status,
and any doubt about the list means that status is read, as before.
"""
import json
import time
from decimal import Decimal as D

import pytest

from src import pmus_request_budget as RB
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

S1, S2 = "aec-atp-a-b-2026-11-25", "aec-atp-c-d-2026-11-25"


@pytest.fixture
def f(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(R, "QTY", D("1"))
    monkeypatch.setattr(M, "TOTAL_CAP", D("50"))
    monkeypatch.setattr(M, "MIN_TOUCH_QTY", D("0"))
    monkeypatch.setattr(M, "FILL_LIST_POLL", True)
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    v = FakeVenue()
    v.bid, v.ask = D("0.45"), D("0.47")
    v.exact, v.lists = [], 0
    real_status = v.status

    def status(oid):
        v.exact.append(oid)
        return real_status(oid)
    v.status = status
    v.list_rows = None                    # None = build from the fake book

    def open_orders():
        v.lists += 1
        if isinstance(v.list_rows, Exception):
            raise v.list_rows
        if v.list_rows is not None:
            return {"rows": dict(v.list_rows)}
        return {"rows": {oid: {"id": oid, "marketSlug": o["slug"],
                               "state": "ORDER_STATE_NEW",
                               "quantity": str(o["qty"]),
                               "cumQuantity": str(o["filled"])}
                         for oid, o in v.orders.items() if o["open"]}}
    v.open_orders = open_orders
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e._sleep = lambda s: None
    return e, v


def rest(e, v, slug=S1):
    """Two opening bids resting, old enough to be expected in a list, each
    already read exactly once."""
    e.b.m(slug)
    e.step_market(slug)
    for o in e.b.m(slug)["orders"].values():
        o["placed_at"] = time.time() - 30
    e._oo = None
    e.step_market(slug)                    # first exact read of each
    v.exact.clear()
    v.lists = 0
    e._oo = None
    return [oid for oid, o in e.b.m(slug)["orders"].items() if o["open"]]


def logs(capsys, kind):
    pre = "PMUS_TWO_SIDED "
    return [json.loads(l[len(pre):]) for l in capsys.readouterr().out
            .splitlines() if l.startswith(pre) and '"%s"' % kind in l]


def test_off_by_default_every_open_order_is_read_each_pass(f, monkeypatch):
    e, v = f
    oids = rest(e, v)
    monkeypatch.setattr(M, "FILL_LIST_POLL", False)
    e.step_market(S1)
    assert sorted(v.exact) == sorted(oids) and v.lists == 0


def test_the_code_default_is_off():
    import importlib
    import os
    assert os.getenv("PMUS_TWO_SIDED_FILL_LIST_POLL") is None
    assert importlib.reload(M).FILL_LIST_POLL is False


def test_resting_orders_the_list_vouches_for_are_not_read(f):
    e, v = f
    oids = rest(e, v)
    assert len(oids) == 2
    for _ in range(3):
        e.step_market(S1)
    assert v.exact == [] and v.lists == 1          # one list, no polls
    assert e._fill_stats()["skipped"] == 6
    assert all(o["open"] for o in e.b.m(S1)["orders"].values())


def test_an_order_gone_from_the_list_is_read_at_once_and_its_fill_booked(f):
    e, v = f
    oids = rest(e, v)
    hit = oids[0]
    side = e.b.m(S1)["orders"][hit]["side"]
    v.orders[hit]["filled"], v.orders[hit]["open"] = D("1"), False
    e.step_market(S1)
    assert hit in v.exact
    mk = e.b.m(S1)
    assert D(mk["pos"][side]) == D("1")
    assert mk["orders"][hit]["open"] is False


def test_the_list_alone_never_books_a_fill(f):
    """The list says an order is part filled; its own status says nothing
    filled. The status decides."""
    e, v = f
    oids = rest(e, v)
    v.list_rows = {oid: {"id": oid, "state": "ORDER_STATE_NEW",
                         "quantity": "1", "cumQuantity": "1"}
                   for oid in oids}
    e.step_market(S1)
    assert sorted(v.exact)[:2] == sorted(oids)     # both re-read exactly
    mk = e.b.m(S1)
    assert D(mk["pos"]["YES"]) == D("0") and D(mk["pos"]["NO"]) == D("0")


def test_a_changed_filled_quantity_forces_the_exact_read(f, monkeypatch):
    e, v = f
    monkeypatch.setattr(R, "QTY", D("2"))
    oids = rest(e, v)
    hit = oids[0]
    v.orders[hit]["filled"] = D("1")               # part filled, still open
    o = e.b.m(S1)["orders"][hit]
    assert e.fill_list_proves_unchanged(hit, o) is False    # 1 != 0
    e.step_market(S1)
    assert v.exact.count(hit) >= 1
    assert D(o["filled"]) == D("1")                # booked from the status
    if o["open"]:                                  # list now agrees: 1 == 1
        e._oo = None
        assert e.fill_list_proves_unchanged(hit, o) is True


@pytest.mark.parametrize("row", [
    {"state": "ORDER_STATE_NEW", "quantity": "1"},              # no fill field
    {"state": "ORDER_STATE_NEW", "cumQuantity": "junk"},
    {"state": "ORDER_STATE_NEW", "cumQuantity": "-1"},
    {"state": "ORDER_STATE_NEW", "cumQuantity": ""},
    {"state": "ORDER_STATE_PENDING_CANCEL", "cumQuantity": "0"},
    {"state": "ORDER_STATE_PARTIALLY_FILLED", "cumQuantity": "0"},
    {"state": "ORDER_STATE_CANCELED", "cumQuantity": "0"},
    {"state": "ORDER_STATE_FILLED", "cumQuantity": "0"},
    {"state": "ORDER_STATE_EXPIRED", "cumQuantity": "0"},
    {"state": "ORDER_STATE_REJECTED", "cumQuantity": "0"},
])
def test_a_row_that_does_not_prove_it_is_read_exactly(f, row):
    e, v = f
    oids = rest(e, v)
    v.list_rows = {oid: dict(row, id=oid) for oid in oids}
    e.step_market(S1)
    assert sorted(set(v.exact)) == sorted(oids)


def test_leaves_quantity_is_accepted_as_proof(f):
    e, v = f
    oids = rest(e, v)
    v.list_rows = {oid: {"id": oid, "state": "ORDER_STATE_NEW",
                         "quantity": "1", "leavesQuantity": "1"}
                   for oid in oids}
    e.step_market(S1)
    assert v.exact == []


def test_an_order_newer_than_the_list_is_read_exactly(f):
    e, v = f
    oids = rest(e, v)
    for o in e.b.m(S1)["orders"].values():
        o["placed_at"] = time.time() + 5           # after the list read
    e.step_market(S1)
    assert sorted(set(v.exact)) == sorted(oids)


def test_an_order_never_read_exactly_is_read_first(f):
    e, v = f
    oids = rest(e, v)
    e._exact_at = {}
    e.step_market(S1)
    assert sorted(v.exact) == sorted(oids)
    v.exact.clear()
    e.step_market(S1)
    assert v.exact == []


def test_a_failed_list_read_falls_back_to_exact_reads(f, capsys):
    e, v = f
    oids = rest(e, v)
    v.list_rows = OSError("venue down")
    capsys.readouterr()
    e.step_market(S1)
    assert sorted(v.exact) == sorted(oids)
    assert e._fill_stats()["list_errors"] == 1
    got = logs(capsys, "fill_list_error")
    assert got and "exactly" in got[0]["effect"]
    v.list_rows = None                             # and it recovers
    v.exact.clear()
    e._oo = None
    e.step_market(S1)
    assert v.exact == []


def test_a_venue_without_a_list_read_changes_nothing(f):
    e, v = f
    oids = rest(e, v)
    del v.open_orders
    e.step_market(S1)
    assert sorted(v.exact) == sorted(oids)


def test_a_stale_list_proves_nothing(f, monkeypatch):
    e, v = f
    oids = rest(e, v)
    e.step_market(S1)
    assert v.exact == []
    e._oo["t"] -= M.FILL_LIST_MAX_AGE_S + 1        # could not be refreshed
    monkeypatch.setattr(M, "FILL_LIST_REFRESH_S", 1e9)
    e.step_market(S1)
    assert sorted(v.exact) == sorted(oids)


def test_every_order_is_still_read_exactly_once_a_minute(f, monkeypatch):
    e, v = f
    oids = rest(e, v)
    e.step_market(S1)
    assert v.exact == []
    for oid in oids:
        e._exact_at[oid] -= M.FILL_EXACT_EVERY_S + 1
    e.step_market(S1)
    assert sorted(v.exact) == sorted(oids)
    v.exact.clear()
    e.step_market(S1)
    assert v.exact == []


def test_one_list_read_serves_every_market_in_a_pass(f):
    e, v = f
    a = rest(e, v, S1)
    b = rest(e, v, S2)
    for oid in a:                                  # rest() cleared the list
        e._exact_at[oid] = time.monotonic()
    v.exact.clear()
    v.lists = 0
    e._oo = None
    for slug in (S1, S2, S1, S2):
        e.step_market(slug)
    assert v.exact == [] and v.lists == 1
    assert len(a) + len(b) == 4


def test_signed_reads_for_sixteen_markets(f, monkeypatch):
    """The scaling case: 16 markets with resting bids (as many as the
    exposure caps allow). Before: one status read per bid per pass. With
    the list: one read for all of them."""
    e, v = f
    monkeypatch.setattr(M, "MAX_MARKETS", 16)
    monkeypatch.setattr(M, "MAX_TRACKED", 32)
    slugs = ["aec-atp-m%02d-x-2026-11-25" % i for i in range(16)]
    n = sum(len(rest(e, v, s_)) for s_ in slugs)
    for mk in e.b.d["markets"].values():
        for oid, o in mk["orders"].items():
            if o["open"]:
                e._exact_at[oid] = time.monotonic()
    assert n >= 16
    v.exact.clear()
    v.lists = 0
    e._oo = None
    for s_ in slugs:
        e.step_market(s_)
    assert (len(v.exact), v.lists) == (0, 1)
    monkeypatch.setattr(M, "FILL_LIST_POLL", False)
    for s_ in slugs:
        e.step_market(s_)
    assert len(v.exact) == n


def test_the_safety_refresh_always_reads_exactly(f):
    e, v = f
    oids = rest(e, v)
    e.refresh_fills(S1)
    assert sorted(v.exact) == sorted(oids)


def test_the_heartbeat_counts_what_was_saved(f):
    e, v = f
    rest(e, v)
    e.step_market(S1)
    st = e._fill_stats()
    assert st["skipped"] == 2 and st["list_reads"] >= 1


def test_the_row_field_names_are_logged_once(f, capsys):
    e, v = f
    rest(e, v)
    e._oo_keys_logged = False
    capsys.readouterr()
    e.step_market(S1)
    e._oo = None
    e.step_market(S1)
    got = logs(capsys, "fill_list_row_keys")
    assert len(got) == 1 and "cumQuantity" in got[0]["keys"]


# ------------------------------------------------- the list read itself
class _T:
    def __init__(self, answer, budget=True):
        self.answer, self.calls = answer, []
        if budget:
            self.budget = object()

    def request(self, method, path, *a, **kw):
        self.calls.append((method, path, kw))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _venue(t):
    v = M.Venue.__new__(M.Venue)
    v.t = t
    return v


def test_the_list_read_asks_for_the_critical_class():
    t = _T({"orders": [{"id": "A", "cumQuantity": "0"}, {"noid": 1}, "x"]})
    got = _venue(t).open_orders()
    assert list(got["rows"]) == ["A"]
    assert t.calls == [("GET", "/v1/orders/open",
                        {"budget_class": "critical"})]


def test_a_plain_transport_is_not_sent_the_budget_argument():
    t = _T({"orders": []}, budget=False)
    assert _venue(t).open_orders() == {"rows": {}}
    assert t.calls == [("GET", "/v1/orders/open", {})]


@pytest.mark.parametrize("answer", [None, {}, {"orders": None}, [],
                                    {"orders": {"A": {}}}])
def test_an_unreadable_list_raises(answer):
    with pytest.raises(ValueError):
        _venue(_T(answer)).open_orders()


class _Inner:
    def __init__(self):
        self.seen = []

    def request(self, method, path, *a, **kw):
        self.seen.append((method, path, dict(kw)))
        return {"orders": []}


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _budget(**kw):
    c = _Clock()
    return RB.RequestBudget(clock=c, sleep=c.sleep, **kw)


def test_a_list_read_marked_critical_is_never_refused():
    """Ordinary GET /v1/orders/open is "normal" and can be deferred; the
    fill list must not be, or the bot would fall back to per-order polls
    exactly when the budget is tight."""
    b = _budget(rps=1, burst=2)
    b.note_429()                                   # cooling down
    inner = _Inner()
    g = RB.GovernedTransport(inner, b)
    assert RB.classify("GET", "/v1/orders/open") == "normal"
    for _ in range(10):
        g.request("GET", "/v1/orders/open", budget_class="critical")
    assert len(inner.seen) == 10
    assert b.stats["sent"]["critical"] == 10
    assert all("budget_class" not in kw for _m, _p, kw in inner.seen)


def test_only_critical_can_be_asked_for():
    """The argument can raise a read, never lower one."""
    b = _budget(rps=1, burst=2)
    inner = _Inner()
    g = RB.GovernedTransport(inner, b)
    g.request("POST", "/v1/order/X1/cancel", {}, budget_class="research")
    assert b.stats["sent"]["critical"] == 1
    assert "budget_class" not in inner.seen[-1][2]


# ------------------- reconciliation and the heartbeat during a long audit
# LIVE 2026-11-25: while the hourly audit ran (about ten minutes) the
# orphan sweep and the release of reconciled reservations waited for it
# (capital reserved up to 402 s) and no heartbeat was logged. Owner:
# "Prevent hourly audits from blocking timely order reconciliation while
# preserving audit integrity."
def _svc(e, monkeypatch):
    calls = []
    monkeypatch.setattr(e, "enforce_lock", lambda: None)
    monkeypatch.setattr(e, "drain_ws_events", lambda: None)
    monkeypatch.setattr(e, "safety_check", lambda acc: None)
    monkeypatch.setattr(e, "active", lambda: [])
    monkeypatch.setattr(e, "report", lambda: calls.append("report"))
    return calls


def test_the_service_pass_reconciles_orders(f, monkeypatch):
    e, v = f
    calls = _svc(e, monkeypatch)
    v.acct = {"positions": {}, "open_orders": []}
    seen = []
    monkeypatch.setattr(e, "orphan_sweep",
                        lambda orders=None: seen.append(("sweep", orders)))
    monkeypatch.setattr(e, "release_reconciled_reservations",
                        lambda t, acct=None: seen.append(("release", acct)))
    e._last_service = e._svc_acct_at = e._last_orphan = -1e9
    e._last_hb = time.monotonic()
    e.service_pass()
    assert [k for k, _x in seen] == ["sweep", "release"]
    assert seen[0][1] == [] and seen[1][1] is e._acct_cache
    assert calls == []                           # heartbeat not due


def test_the_service_pass_keeps_the_30_second_cadence(f, monkeypatch):
    e, v = f
    _svc(e, monkeypatch)
    v.acct = {"positions": {}, "open_orders": []}
    n = []
    monkeypatch.setattr(e, "orphan_sweep", lambda orders=None: n.append(1))
    e._last_hb = time.monotonic()
    for _ in range(3):
        e._last_service = e._svc_acct_at = -1e9
        e.service_pass()
    assert len(n) == 1


def test_a_stale_account_read_releases_nothing_in_the_service_pass(
        f, monkeypatch):
    e, v = f
    _svc(e, monkeypatch)
    monkeypatch.setattr(e, "read_account", lambda: None)   # read failed
    e._acct_cache, e._acct_ok_at = None, None
    out = []
    monkeypatch.setattr(e, "orphan_sweep", lambda orders=None: out.append(
        ("sweep", orders)))
    monkeypatch.setattr(e, "release_reconciled_reservations",
                        lambda *a, **k: out.append("release"))
    e._last_service = e._svc_acct_at = e._last_orphan = -1e9
    e._last_hb = time.monotonic()
    e.service_pass()
    assert out == [("sweep", None)]              # swept; nothing released


def test_the_heartbeat_is_logged_from_the_service_pass(f, monkeypatch):
    e, v = f
    calls = _svc(e, monkeypatch)
    v.acct = {"positions": {}, "open_orders": []}
    monkeypatch.setattr(e, "reconcile_orders_now", lambda: None)
    e._last_hb = time.monotonic() - 61
    e._last_service = -1e9
    e.service_pass()
    e._last_service = -1e9
    e.service_pass()
    assert calls == ["report"]                   # once, then not due


def test_a_failing_heartbeat_does_not_stop_order_service(f, monkeypatch):
    e, v = f
    _svc(e, monkeypatch)
    monkeypatch.setattr(e, "reconcile_orders_now", lambda: None)

    def boom():
        raise RuntimeError("report failed")
    monkeypatch.setattr(e, "report", boom)
    stepped = []
    monkeypatch.setattr(e, "active", lambda: [S1])
    monkeypatch.setattr(e, "step_market", lambda s: stepped.append(s))
    e._last_hb = -1e9
    e._last_service = -1e9
    e.service_pass()
    assert stepped == [S1]

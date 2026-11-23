"""Owner 2026-11-23: "Make safety independent of Claude's session ...
reconciliation failures must trigger a durable halt." "A controller that
doesn't promptly know its inventory cannot safely accumulate more."

LIVE 2026-11-22 22:10Z: a fill was recorded 89 s after the venue made it.
The position check then agreed again, nothing latched, and the manual
relock reached the service 22 minutes later.
"""
import json
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_safety_latch import hold, row, snapshot
from tests.test_pmus_sports_two_sided import FakeVenue

SLUG = "aec-x"


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    monkeypatch.delenv(M.SAFETY_LATCH_ACK_ENV, raising=False)
    v = FakeVenue()
    v.states = {}
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    return e, v


def logged(capsys, name):
    pre = "PMUS_TWO_SIDED "
    rows = [json.loads(l[len(pre):])
            for l in capsys.readouterr().out.splitlines()
            if l.startswith(pre)]
    return [r for r in rows if r.get("kind") == name]


def fill_with_stamp(v, age_s):
    """Fill every resting order in full; the venue's stamp on the order is
    `age_s` seconds old when the bot reads it."""
    base = v.status

    def status(oid):
        st = base(oid)
        st["t"] = time.time() - age_s
        return st
    v.status = status
    for o in v.orders.values():
        if o["open"]:
            o["filled"], o["open"] = o["qty"], False


# ------------------------------------------------------------ late fill
def test_every_fill_reports_how_late_it_was_seen(eng, capsys):
    e, v = eng
    e.b.m(SLUG)
    e.step_market(SLUG)
    fill_with_stamp(v, 89)
    e.step_market(SLUG)
    fills = logged(capsys, "FILL")
    assert fills and all(88 <= f["seen_late_s"] <= 91 for f in fills)
    assert not e.b.d.get("safety_latch")          # measuring only by default


def test_a_fill_seen_late_latches_the_bot(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "FILL_LATE_LATCH_S", 30.0)
    e.b.m(SLUG)
    e.step_market(SLUG)
    fill_with_stamp(v, 89)
    e.step_market(SLUG)
    lt = e.b.d["safety_latch"]
    assert lt["why"] == "fill_seen_late"
    assert float(lt["detail"]["late_s"]) > 30
    # the fill itself is still recorded: the book must not lose inventory
    mk = e.b.m(SLUG)
    assert D(mk["pos"]["YES"]) + D(mk["pos"]["NO"]) > 0
    # durable: nothing is placed after it, at any stage
    n = len(v.orders)
    e.place(SLUG, "YES", D("0.40"), D("2"), "rn1_open")
    e.place(SLUG, "NO", D("0.40"), D("2"), "two_sided_complete")
    assert len(v.orders) == n


def test_a_fill_seen_promptly_does_not_latch(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "FILL_LATE_LATCH_S", 30.0)
    e.b.m(SLUG)
    e.step_market(SLUG)
    fill_with_stamp(v, 6)
    e.step_market(SLUG)
    assert not e.b.d.get("safety_latch")


def test_a_fill_with_no_venue_stamp_does_not_latch(eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "FILL_LATE_LATCH_S", 30.0)
    e.b.m(SLUG)
    e.step_market(SLUG)
    for o in v.orders.values():
        o["filled"], o["open"] = o["qty"], False
    e.step_market(SLUG)
    fills = logged(capsys, "FILL")
    assert fills and all(f["seen_late_s"] is None for f in fills)
    assert not e.b.d.get("safety_latch")


# ------------------------------------------------------- failed audits
def test_one_failed_audit_blocks_new_exposure_and_brings_the_next_forward(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "AUDIT_LATCH", True)
    e._last_reward = time.monotonic()
    e.audit_result("position_mismatch", True, mismatches=[{"slug": SLUG}])
    assert not e.b.d.get("safety_latch")
    assert e.audit_blocks() == "audit_failed:position_mismatch"
    due_in = M.REWARD_CHECK_S - (time.monotonic() - e._last_reward)
    assert due_in <= M.AUDIT_RECHECK_S + 1
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert not [o for o in v.orders.values() if o["open"]]
    q = logged(capsys, "RN1_QUOTE")
    assert q and q[-1]["block"] == "audit_failed:position_mismatch"


def test_a_clean_audit_clears_the_count(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "AUDIT_LATCH", True)
    e.audit_result("position_mismatch", True)
    e.audit_result("position_mismatch", False)
    assert e.audit_blocks() is None
    e.audit_result("position_mismatch", True)
    assert not e.b.d.get("safety_latch")          # one in a row again


def test_the_same_check_failing_twice_in_a_row_latches(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "AUDIT_LATCH", True)
    e.audit_result("fills_unconfirmed", True, unconfirmed=[SLUG])
    e.audit_result("position_mismatch", True)     # a different check
    assert not e.b.d.get("safety_latch")
    e.audit_result("fills_unconfirmed", True, unconfirmed=[SLUG])
    lt = e.b.d["safety_latch"]
    assert lt["why"] == "audit_fills_unconfirmed"
    assert SLUG in lt["detail"]["unconfirmed"]


def test_off_by_default_nothing_blocks_or_latches(eng):
    e, v = eng
    for _ in range(3):
        e.audit_result("position_mismatch", True)
    assert e.audit_blocks() is None and not e.b.d.get("safety_latch")


def test_the_position_audit_feeds_the_count_only_on_a_complete_walk(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "AUDIT_LATCH", True)
    hold(e, SLUG, yes=2, cy="0.9")
    snapshot(v, [row(SLUG, 4, 0)], complete=False)   # cannot prove anything
    e.inventory_reconcile()
    assert e.audit_blocks() is None
    snapshot(v, [row(SLUG, 4, 0)])                   # venue 4, book 2
    e.inventory_reconcile()
    assert e.audit_blocks() == "audit_failed:position_mismatch"
    assert not e.b.d.get("safety_latch")
    e.inventory_reconcile()
    assert e.b.d["safety_latch"]["why"] == "audit_position_mismatch"


def test_a_matching_position_audit_never_counts(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "AUDIT_LATCH", True)
    hold(e, SLUG, yes=2, cy="0.9")
    snapshot(v, [row(SLUG, 2, 0)])
    for _ in range(3):
        e.inventory_reconcile()
    assert e.audit_blocks() is None and not e.b.d.get("safety_latch")


# ------------------------------- the audit no longer holds orders still
class PagedTransport:
    """Stands in for the venue transport during a long read-only walk."""

    def __init__(self, fail=False):
        self.n, self.fail = 0, fail

    def request(self, method, path, **kw):
        self.n += 1
        if self.fail:
            raise RuntimeError("venue said no")
        return {"activities": [], "eof": True}

    marker = "the wrapped transport's own attribute"


def fill_all(v):
    for o in v.orders.values():
        if o["open"]:
            o["filled"], o["open"] = o["qty"], False


def due(e):
    e._last_service = time.monotonic() - M.LOOP_S - 1
    e._svc_acct_at = time.monotonic()        # the account read is not due


@pytest.fixture
def quoting(eng, monkeypatch):
    e, v = eng
    e.guard = None
    mk = e.b.m(SLUG)
    e.step_market(SLUG)
    assert [o for o in v.orders.values() if o["open"]]
    return e, v, mk


def test_a_fill_during_the_audit_walk_is_recorded_between_its_requests(
        quoting, capsys):
    e, v, mk = quoting
    v.t = PagedTransport()
    t = e.audit_transport()
    assert isinstance(t, M.ServicedTransport) and t.marker == v.t.marker
    fill_all(v)                               # filled while the audit runs
    due(e)
    t.request("GET", "/v1/portfolio/activities")
    assert v.t.n == 1
    assert D(mk["pos"]["YES"]) + D(mk["pos"]["NO"]) > 0
    assert logged(capsys, "FILL")


def test_orders_are_served_even_when_an_audit_request_fails(quoting):
    e, v, mk = quoting
    v.t = PagedTransport(fail=True)
    fill_all(v)
    due(e)
    with pytest.raises(RuntimeError):
        e.audit_transport().request("GET", "/x")
    assert D(mk["pos"]["YES"]) + D(mk["pos"]["NO"]) > 0


def test_markets_are_stepped_at_the_loop_pace_not_on_every_request(
        quoting, monkeypatch):
    e, v, mk = quoting
    steps = []
    monkeypatch.setattr(e, "step_market", lambda slug: steps.append(slug))
    due(e)
    e.service_pass()
    e.service_pass()
    e.service_pass()
    assert steps == [SLUG]                    # once per LOOP_S
    due(e)
    e.service_pass()
    assert steps == [SLUG, SLUG]


def test_the_service_pass_never_re_enters_itself(quoting, monkeypatch):
    e, v, mk = quoting
    depth = []

    def step(slug):
        depth.append(len(depth))
        due(e)
        e.service_pass()                      # a nested long read
    monkeypatch.setattr(e, "step_market", step)
    due(e)
    e.service_pass()
    assert depth == [0]


def test_a_latched_bot_cancels_and_steps_nothing_during_the_audit(
        quoting, monkeypatch):
    e, v, mk = quoting
    steps = []
    e.latch("position_mismatch", slug=SLUG)
    monkeypatch.setattr(e, "step_market", lambda slug: steps.append(slug))
    due(e)
    e.service_pass()
    assert steps == []
    assert not [o for o in v.orders.values() if o["open"]]


def test_interleaving_can_be_switched_off(quoting, monkeypatch):
    e, v, mk = quoting
    v.t = PagedTransport()
    monkeypatch.setattr(M, "AUDIT_INTERLEAVE", False)
    assert e.audit_transport() is v.t


# ---------------------- a loop that reads but does not serve its orders
@pytest.fixture
def clocks(monkeypatch):
    monkeypatch.setitem(M._LOOP, "thread", None)
    monkeypatch.setitem(M._LOOP, "beat", time.time())     # requests flowing
    monkeypatch.setitem(M._LOOP, "max_gap", 0.0)
    monkeypatch.setitem(M._ORDERS, "served", time.time() - 87)
    monkeypatch.setitem(M._ORDERS, "max_gap", 0.0)


def test_the_gap_in_order_service_is_measured_and_reported(clocks):
    r = M.loop_report(reset=False)
    assert r["idle_s"] < 2                    # the old figure sees nothing
    assert 86 <= r["order_service_idle_s"] <= 89
    assert r["order_service_max_gap_s"] >= 86
    assert r["stalled"] is False              # reporting only by default


def test_with_a_limit_the_gap_reads_as_a_stalled_loop(clocks, monkeypatch):
    monkeypatch.setattr(M, "ORDER_SERVICE_STALL_S", 45.0)
    assert M.order_service_stalled() is True
    assert M.loop_report(reset=False)["stalled"] is True
    M.orders_served()
    assert M.order_service_stalled() is False
    assert M.loop_report(reset=False)["stalled"] is False


def test_the_registry_tells_the_watchdog_when_orders_are_not_served(
        eng, clocks, monkeypatch):
    e, v = eng
    doc = M.registry_doc(e.b)
    assert doc["manages_own_resting"] is True
    monkeypatch.setattr(M, "ORDER_SERVICE_STALL_S", 45.0)
    doc = M.registry_doc(e.b)
    assert doc["manages_own_resting"] is False and doc["loop_stalled"] is True
    assert 86 <= doc["order_service_idle_s"] <= 89

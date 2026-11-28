"""Does the freshness telemetry actually produce the six numbers?

The owner's next step after the instrumentation landed: **validate it in
disarmed mode** before any deployment. So this drives the REAL method
bodies -- `Runner.maybe_book_completion`, `Runner.poll_orders`,
`Runner.calibration_live_tick` -- against a real `InventoryStore` holding
a real one-sided position and a book at a known stream age, and asserts
the report that comes out is populated and correct.

WHY A STUB SELF. A Runner cannot be constructed without a venue client,
so this suite's established pattern is to bind the real unbound methods to
an object carrying only the attributes they touch. That is also the seam
that matters here: the telemetry has to work on the same store and book
the executor uses, not on a mock of them.

DISARMED THROUGHOUT. No transport that can submit, no credentials, no
network, no order. `maybe_book_completion` is allowed to reach its
decision and refuse; nothing downstream of a decision is exercised.
"""
from __future__ import annotations

import collections
import os
from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC
from src import l5_completion_latency as CL
from src import l5_fill_calibration as CALIB
from src import l5_live_execution as LX
from src import l5_pmus_live_runner as RUN
from src import l5_two_sided_probe as TS

SLUG = "cpc-btc-updown-15m-2026-11-08-1200z"
NEXT_SLUG = "cpc-btc-updown-15m-2026-11-08-1215z"
WS = 1794139200.0            # the window's start, :00


class Book:
    """`snapshot(slug)` and `trades(slug)`, with a settable stream age."""

    def __init__(self, age=0.4, bid="0.62", ask="0.63"):
        self.age, self.bid, self.ask = age, bid, ask

    def snapshot(self, slug):
        return {"bid": D(self.bid), "ask": D(self.ask),
                "stream_age_s": D(str(self.age)),
                "bid_shares": D("400"), "ask_shares": D("400")}

    def trades(self, slug):
        return []


class Gates:
    max_worst_case_deficit_usd = D("1.00")


class Transport:
    """NO submit, NO cancel, NO network. Deliberately not even a method
    that could place an order, so the harness cannot trade by accident."""
    tick_size = "0.01"
    quantity_increment = "1"

    def __init__(self, slug=SLUG):
        self.market_slug = slug


class Stub:
    LATENCY_OBSERVER_KEY = RUN.Runner.LATENCY_OBSERVER_KEY

    def __init__(self, tmp_path, *, age=0.4):
        self.transport = Transport()
        self.store = LX.InventoryStore(
            os.path.join(str(tmp_path), "inv.json"))
        self.book = Book(age=age)
        self.executor = type("E", (), {"validation_gates": Gates(),
                                       "store": self.store})()
        self.stats = collections.Counter()
        self.win = collections.Counter()
        self.shadow_ladder = None
        self.last_order_poll = {}
        self._last_action_row = {}
        self._book_completion_t = 0.0
        self._latency_last_slug = None
        self.now_t = WS + 60.0
        self.completion_latency = CL.CompletionLatency()
        self.fill_calibration = CALIB.CalibrationRecorder(
            str(tmp_path), path=os.path.join(str(tmp_path), "cal.ndjson"))

    # the real bodies under test
    maybe_book_completion = RUN.Runner.maybe_book_completion
    poll_orders = RUN.Runner.poll_orders
    calibration_live_tick = RUN.Runner.calibration_live_tick
    _latency_safe = RUN.Runner._latency_safe
    _latency_observer_note = RUN.Runner._latency_observer_note
    _latency_report = RUN.Runner._latency_report
    _bc_capture = RUN.Runner._bc_capture

    def lifecycle(self, events):
        for e in events or ():
            self.stats["LIFECYCLE:%s" % (e.get("event") or "ERROR")] += 1

    # HELD BY A REAL ONE-SIDED UP POSITION, at a cost basis whose
    # complement is dearer than the 0.96 pair cap. So `decide` reaches the
    # freshness check, passes it, and refuses on ECONOMICS -- which is both
    # the realistic production case and the only way this harness can be
    # sure it never touches a submission path. The executor stub carries no
    # `handle` at all, and a test below asserts no decision ever returns
    # ok True here.
    def hold_up(self, qty="1", cost="0.72"):
        self.store.state.setdefault("positions", {})[
            "%s|UP" % SLUG] = qty
        self.store.state.setdefault("leg_cost", {})[
            "%s|UP" % SLUG] = cost


@pytest.fixture
def armed_env(monkeypatch):
    """ROUTE B switched on. This enables the DECISION only -- submission
    needs an armed transport and an owner authorization, neither of which
    exists in this harness."""
    monkeypatch.setenv(BC.ENABLED_ENV, "YES")
    assert BC.enabled() is True
    yield


def drive(s, *, ticks, every=5.5, start=None):
    """Run `maybe_book_completion` on a cadence, as the tick loop does."""
    t = (WS + 60.0) if start is None else start
    for _ in range(ticks):
        s.now_t = t
        s.maybe_book_completion(t)
        t += every
    return t


# =================================================== it populates at all
def test_the_report_is_populated_by_real_decisions(armed_env, tmp_path):
    s = Stub(tmp_path)
    s.hold_up()
    drive(s, ticks=6)
    r = s.completion_latency.report(SLUG)
    assert r["known"] is True
    assert r["decisions"] >= 5, r
    # item 5: every decision carries its own reason
    assert r["decision_reasons"], r


def test_item_2_book_age_is_recorded_on_decisions_that_pass_freshness(
        armed_env, tmp_path):
    """The whole point of the fix: a decision taken on a FRESH book now
    carries the age it was taken on, measured on this process's clock --
    not inferred from a 6.4-6.9s recorded ladder."""
    s = Stub(tmp_path, age=0.4)
    s.hold_up()
    drive(s, ticks=6)
    a = s.completion_latency.report(SLUG)["book_age_at_decision_s"]
    assert a["n"] >= 5, a
    assert a["median"] == 0.4 and a["max"] == 0.4


def test_a_decision_on_a_stale_book_is_recorded_as_stale_with_its_age(
        armed_env, tmp_path):
    s = Stub(tmp_path, age=TS.MAX_BOOK_AGE_S + 4.5)
    s.hold_up()
    drive(s, ticks=4)
    r = s.completion_latency.report(SLUG)
    assert "STALE_BOOK" in r["decision_reasons"], r["decision_reasons"]
    assert r["book_age_at_decision_s"]["max"] == TS.MAX_BOOK_AGE_S + 4.5


def test_the_p95_separates_a_tail_of_stale_evaluations_from_the_median(
        armed_env, tmp_path):
    """A 2.0 s requirement met on average and missed at the tail must not
    read as met. This is the case the recorded ladders could never show."""
    s = Stub(tmp_path, age=0.3)
    s.hold_up()
    t = drive(s, ticks=8)
    s.book.age = 6.8
    drive(s, ticks=2, start=t)
    a = s.completion_latency.report(SLUG)["book_age_at_decision_s"]
    assert a["median"] == 0.3
    assert a["max"] == 6.8
    assert a["median"] <= float(TS.MAX_BOOK_AGE_S) < a["max"]


# ======================================================= items 3 and 4
def test_item_3_detection_to_decision_is_measured(armed_env, tmp_path):
    s = Stub(tmp_path)
    s.hold_up()
    drive(s, ticks=5)
    r = s.completion_latency.report(SLUG)
    assert r["detection_to_decision_s"]["n"] >= 4
    assert r["detection_to_decision_s"]["max"] >= 11.0


def test_detection_is_stamped_on_the_first_tick_that_sees_the_position(
        armed_env, tmp_path):
    s = Stub(tmp_path)
    s.hold_up()
    drive(s, ticks=3)
    assert s.completion_latency.m[SLUG]["detected_t"] == WS + 60.0
    assert s.completion_latency.m[SLUG]["detected_side"] == "UP"


def test_no_position_means_no_detection_and_no_false_interval(
        armed_env, tmp_path):
    """A market we hold nothing in must not report a fill detection."""
    s = Stub(tmp_path)
    drive(s, ticks=3)
    m = s.completion_latency.m.get(SLUG)
    assert m is None or m["detected_t"] is None


# ============================================================== item 1
def test_item_1_the_poll_cadence_is_measured_from_real_polls(tmp_path):
    """`poll_orders` is the real body. The orders here are ledger rows
    with a venue id and no transport call that can succeed, which is
    enough to time the cadence -- the bound on fill detection."""
    s = Stub(tmp_path)
    s.store.record_submit("l5f-aaa", {"market": SLUG, "side": "UP",
                                      "price": "0.62", "qty": "1"})
    s.store.state["live_orders"]["l5f-aaa"]["venue_order_id"] = "V-1"
    # NOT starting at 0.0: `poll_orders` keeps a per-order interval floor
    # and `last_order_poll` defaults to 0.0, so a first call at t=0 is
    # correctly skipped as "polled already". Starting at 10 s means every
    # call below actually polls, which is what makes the gaps real.
    for t in (10.0, 16.2, 22.5, 35.0):
        s.now_t = t
        s.poll_orders()
    g = s.completion_latency.report(SLUG)["poll_gaps_s"]
    assert g["n"] >= 3, g
    assert g["max"] == 12.5          # the real worst gap, not ORDER_POLL_S
    assert (s.completion_latency.report(SLUG)["fill_detection_latency"]
            ["measurable"] is False)


# ============================================================== item 6
def test_item_6_the_observer_continues_across_a_real_rollover(tmp_path):
    """`calibration_live_tick` is the real body, and the heartbeat is
    taken on the ticks where there is nothing to observe too."""
    s = Stub(tmp_path)
    for t in (10.0, 11.0, 12.0):
        s.now_t = t
        s.calibration_live_tick(t)
    s.transport.market_slug = NEXT_SLUG          # the window rolled
    for t in (13.0, 14.0):
        s.now_t = t
        s.calibration_live_tick(t)
    obs = s._latency_report()["observer"]
    assert obs["rollovers"] == 1
    assert obs["observations_before_roll"] == 3
    assert obs["observations_after_roll"] == 2
    assert obs["observer_continued_across_rollover"] is True


def test_the_heartbeat_is_taken_with_no_live_orders_at_all(tmp_path):
    """Disarmed, this is the ONLY item that populates -- which is itself
    the finding, and it has to be distinguishable from a dead observer."""
    s = Stub(tmp_path)
    assert not s.store.live_orders()
    s.now_t = 5.0
    s.calibration_live_tick(5.0)
    assert s._latency_report()["observer"]["observations_before_roll"] == 1


# ================================= what disarmed CANNOT produce, asserted
def test_disarmed_with_no_inventory_produces_only_the_observer(tmp_path):
    """The honest statement in FRESHNESS_TELEMETRY.md, as a test: with no
    inventory and no live orders, items 1-4 cannot populate. If a future
    change made them appear here it would be measuring something that did
    not happen."""
    s = Stub(tmp_path)
    for t in (1.0, 2.0, 3.0):
        s.now_t = t
        s.calibration_live_tick(t)
        s.poll_orders()
    rep = s._latency_report()
    assert rep["observer"]["observations_before_roll"] == 3
    assert rep["markets"] == []


def test_item_4_eligibility_is_never_recorded_as_submitted_here(
        armed_env, tmp_path):
    """Nothing in this harness can submit, so no eligibility record may
    claim it did."""
    s = Stub(tmp_path)
    s.hold_up()
    drive(s, ticks=5)
    for e in s.completion_latency.m.get(SLUG, {}).get("eligible", []):
        assert e["submitted"] is False


def test_the_harness_itself_cannot_place_an_order(armed_env, tmp_path):
    """Guard on the guard, three ways, so a future edit cannot quietly
    turn this validation into a live test."""
    s = Stub(tmp_path)
    for bad in ("submit", "place", "order", "cancel", "amend"):
        assert not hasattr(s.transport, bad), bad
    # the executor stub has no `handle`, which is the only door to a
    # submission from this method
    assert not hasattr(s.executor, "handle")
    # and no decision in this harness approves, so that door is never tried
    s.hold_up()
    for _ in range(6):
        rec = s.maybe_book_completion(s.now_t)
        assert rec is None or rec.get("ok") is not True, rec
        s.now_t += 5.5
    assert not any("book_completion_reentry" in k for k in s.stats)


# ========================================= the telemetry cannot interfere
def test_a_raising_accumulator_does_not_change_a_single_decision(
        armed_env, tmp_path):
    """The decisions must be byte-identical with the telemetry broken."""
    good = Stub(tmp_path / "a")
    good.hold_up()
    drive(good, ticks=6)

    bad = Stub(tmp_path / "b")
    bad.hold_up()

    class Boom:
        def __getattr__(self, _n):
            def _f(*a, **k):
                raise RuntimeError("telemetry exploded")
            return _f
    bad.completion_latency = Boom()
    drive(bad, ticks=6)

    def reasons(s):
        return {k: v for k, v in s.stats.items()
                if k.startswith("book_completion:")}
    assert reasons(good) == reasons(bad)
    assert any(k.startswith("latency_error:") for k in bad.stats), bad.stats

"""SHUTDOWN AND CRASH RECOVERY, BY FAILURE INJECTION.

The handover deadman was already correct: exact-id, ownership-scoped,
verified against the venue, and fail-closed when order state cannot be
read. What was wrong was that `run_cohort` could reach it from exactly ONE
place -- the `past_cutoff` branch -- so SIGTERM, SIGINT, an exception
escaping the loop and a normal exit before the cutoff all returned with
owned orders resting. That is the 2026-10-08 incident: twenty 0.13-share
orders left on the 1230z window because "E1 was disarmed and its process
stopped, which means ITS OWN DEADMAN NEVER RAN".

Every test here INJECTS one of those exits and asserts on the venue, not on
a log line. The ones that matter most are the negative ones: a shutdown
that cannot establish order state must REFUSE and say so, and must never
report a cancellation it did not verify.

WHAT IS NOT TESTED HERE, BECAUSE IT CANNOT BE. No test in this file claims
SIGKILL coverage. A killed process runs nothing, so the only protection is
out-of-process: the durable latch and the startup reconciliation, which get
their own simulated-crash tests at the bottom.
"""
from __future__ import annotations

import os
import signal
from decimal import Decimal as D
from typing import Any, Dict, List

import pytest

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import order_registry as ORG
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import authorization as AUTH
from analysis.l5.e1_cohort import progress as PG
from analysis.l5.e1_cohort import recorder as RC
from analysis.l5.e1_cohort import shutdown as SD
from analysis.l5.e1_cohort import stopping as SP
from analysis.l5.e1_cohort.test_e1_live_path import (FakeTransport, arm,
                                                     fake_pin, BASE)
from analysis.l5.e1_cohort.test_e1_pair_lifecycle import (FakeVenue, UP,
                                                          DOWN)
from analysis.l5.e1_cohort.test_e1_run_cohort_lifecycle import (
    Clock, LoopVenue, _Prog, _Rec, _provider, cancelling_transport)


@pytest.fixture(autouse=True)
def _clean_shutdown_state():
    SD.reset_for_test()
    R._set_emergency_handover(None)
    yield
    SD.reset_for_test()
    R._set_emergency_handover(None)


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    FakeTransport.posts = []
    return fake_pin(BASE)


# =====================================================================
# 1. THE SIGNAL ITSELF, AND AN HONEST STATEMENT OF ITS LIMITS
# =====================================================================

def test_coverage_names_what_it_cannot_protect_against():
    cov = SD.coverage()
    assert cov["in_process_handler_covers_sigkill"] is False
    assert cov["atexit_used"] is False
    for name in ("SIGKILL", "OOM_KILL", "POWER_LOSS",
                 "CONTAINER_HARD_STOP"):
        assert name in cov["NOT_COVERED"]
    # and it says what DOES cover a hard crash, by module
    assert any("latch" in s for s in cov["hard_crash_is_covered_by"])
    assert any("startup reconciliation" in s
               for s in cov["hard_crash_is_covered_by"])


def test_sigterm_raises_and_records(monkeypatch):
    got = SD.install()
    assert got["installed"] == ["SIGTERM", "SIGINT"]
    with pytest.raises(SD.ShutdownRequested) as e:
        os.kill(os.getpid(), signal.SIGTERM)
    assert e.value.detail["signal"] == "SIGTERM"
    assert SD.requested()["signal"] == "SIGTERM"


def test_shutdown_is_a_BaseException_so_the_loop_cannot_swallow_it():
    """THE SHARP ONE. The iteration loop is full of `except Exception`
    classifiers. A shutdown that inherited from `Exception` would be caught
    by the first of them, recorded as a venue error, and the loop would
    carry on PLACING ORDERS during a shutdown."""
    assert issubclass(SD.ShutdownRequested, BaseException)
    assert not issubclass(SD.ShutdownRequested, Exception)
    SD.install()
    swallowed = []
    try:
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception as exc:                              # noqa: BLE001
            swallowed.append(exc)
    except SD.ShutdownRequested:
        pass
    assert swallowed == [], "an `except Exception` must not see a shutdown"


def test_a_second_signal_does_not_abandon_the_handover():
    """A repeat SIGTERM is recorded, not re-raised: re-raising from inside
    the cancel sequence would leave it half-finished."""
    SD.install()
    with pytest.raises(SD.ShutdownRequested):
        os.kill(os.getpid(), signal.SIGTERM)
    os.kill(os.getpid(), signal.SIGTERM)          # must NOT raise
    assert SD.requested()["repeats"], SD.requested()


def test_install_is_idempotent_and_reports_refusals(monkeypatch):
    SD.install()
    again = SD.install()
    assert again["installed"] == ["SIGTERM", "SIGINT"]

    def _refuse(*a, **k):
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(signal, "signal", _refuse)
    out = SD.install()
    assert out["installed"] == []
    assert out["refused"] == {"SIGTERM": "ValueError",
                             "SIGINT": "ValueError"}


# =====================================================================
# 2. INJECTED EXITS: the handover actually cancels, by owned id
# =====================================================================

class InjectedCrash(RuntimeError):
    """Stands in for every exit that runs no handler at all."""


class Wrapped:
    """A delegating provider wrapper carrying the two seams these tests need.

    `__call__` cannot be patched on an INSTANCE -- Python looks special
    methods up on the type -- so my first version's injection never fired
    and three tests passed by doing nothing at all. Both the failure
    injection and the `order_state` seam live on a real type here.
    """

    def __init__(self, inner, venue, *, die_after=None, blind=False):
        self._inner = inner
        self._venue = venue
        self._die_after = die_after
        self._blind = blind
        self._n = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __call__(self):
        self._n += 1
        snap = self._inner()
        if self._die_after is not None and self._n >= self._die_after \
                and self._venue.posted:
            hook = self.__dict__.get("on_die")
            if hook is not None:
                hook()
            raise InjectedCrash("the process is dying mid-window")
        return snap

    # THE RECONCILIATION SEAM. The lifecycle harness's provider has no
    # `order_state`, so `run_cohort` skipped startup reconciliation
    # entirely and my first restart test could never have failed.
    # Production's provider has it, so the harness must too.
    def order_state(self, _pin=None):
        if self._blind:
            raise OSError("venue unreachable")
        return self._inner().get("order_state")

    def go_blind(self):
        self._blind = True


def _wire(wired, clock, *, known, roles, pending, cutoff_at=BASE + 10_000,
          die_after=None):
    venue = LoopVenue(clock, wired["slug"])
    inner = _provider(venue, clock, pin=wired, known=known, roles=roles,
                      pending=pending, cutoff_at=cutoff_at)
    return venue, Wrapped(inner, venue, die_after=die_after)


def test_exception_escaping_the_loop_still_cancels_owned_orders(wired):
    """FAILURE INJECTION: the provider raises once a first leg is resting.

    The cutoff is far away, so the `past_cutoff` branch is unreachable --
    exactly the shape of the 2026-10-08 incident. Before this repair the
    order stayed open; now `main`'s unwind runs the handover.
    """
    clock = Clock(BASE + 10)
    known: List[str] = []
    roles: Dict[str, str] = {}
    venue, prov = _wire(wired, clock, known=known, roles=roles,
                        pending=[], die_after=3)

    tracker = SP.CohortTracker()
    with pytest.raises(InjectedCrash):
        R.run_cohort(snapshot_provider=prov, tracker=tracker, recorder=_Rec(),
                     transport_factory=cancelling_transport(venue),
                     now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                     progress=_Prog(), max_iterations=40)

    # the hook SURVIVED the exception -- that is what makes it reachable
    assert R.emergency_handover_pending() is True
    assert venue.posted, "the injection must happen after a real post"
    oid = venue.posted[0]
    assert venue.orders[oid]["state"] == "ORDER_STATE_NEW"

    rec = R.run_emergency_handover(cause="UNCAUGHT_EXCEPTION")
    assert rec["ran"] is True
    assert rec["deadman"]["targets"] == [oid]
    assert venue.orders[oid]["state"] == "ORDER_STATE_CANCELED"
    assert rec["deadman"]["all_cancelled"] is True
    # and the hook is spent, so a second unwind cannot double-cancel
    assert R.emergency_handover_pending() is False
    assert R.run_emergency_handover(cause="AGAIN") is None


def test_a_foreign_order_is_never_cancelled_by_the_shutdown(wired):
    """OWNERSHIP SCOPE, UNDER SHUTDOWN. The account is shared; a market
    sweep would cancel another desk's orders."""
    clock = Clock(BASE + 10)
    known: List[str] = []
    roles: Dict[str, str] = {}
    venue, prov = _wire(wired, clock, known=known, roles=roles,
                        pending=[], die_after=3)
    # ANOTHER DESK ARRIVES AFTER OUR POST, and it has to be that way round:
    # E3 counts an unattributable resting order as outstanding, so a
    # stranger already sitting there would have blocked our placement and
    # the test would have proved nothing.
    prov.on_die = lambda: venue.rest("STRANGER-1", side=UP, wire="0.42",
                                     qty="16.21", slug=wired["slug"])

    with pytest.raises(InjectedCrash):
        R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                     recorder=_Rec(),
                     transport_factory=cancelling_transport(venue),
                     now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                     progress=_Prog(), max_iterations=40)
    rec = R.run_emergency_handover(cause="UNCAUGHT_EXCEPTION")
    dm = rec["deadman"]
    assert "STRANGER-1" not in dm["targets"]
    assert "STRANGER-1" in dm["foreign_open_ids_left_untouched"]
    assert venue.orders["STRANGER-1"]["state"] == "ORDER_STATE_NEW"
    assert dm["foreign_untouched"] is True


def test_a_normal_exit_before_the_cutoff_still_cancels(wired):
    """THE QUIET CASE, and the one that produced the incident.

    The loop can simply run out of iterations, budget or verdict well
    before the withdrawal cutoff. Every one of those exits used to return
    with our order resting, because the handover lived only on the
    `past_cutoff` branch. It now runs on the way out, and `run_cohort`
    reports it.
    """
    clock = Clock(BASE + 10)
    venue, prov = _wire(wired, clock, known=[], roles={}, pending=[])
    out = R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                       recorder=_Rec(),
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=3)
    assert venue.posted, "the premise: a first leg was placed"
    sh = out["shutdown_handover"]
    assert sh is not None, out.keys()
    assert sh["ran"] is True
    assert sh["cause"].startswith("NORMAL_EXIT_WITHOUT_CUTOFF_HANDOVER")
    for oid in venue.posted:
        assert venue.orders[oid]["state"] == "ORDER_STATE_CANCELED", oid
    assert out["shutdown_coverage"]["in_process_handler_covers_sigkill"] \
        is False
    # the hook is cleared on a normal return, so `main` has nothing to do
    assert R.emergency_handover_pending() is False


# =====================================================================
# 3. FAIL-CLOSED: unreadable state, failed cancels, ambiguous submits
# =====================================================================

def test_no_view_at_all_refuses_and_claims_no_cancellation(wired):
    """WITH NOTHING TO READ, THE HANDOVER REFUSES.

    Driven at the deadman directly, because that is where the branch
    lives and it is the same call the shutdown makes. Without a readable
    view we cannot enumerate what is outstanding, so nothing is cancelled
    and -- the part that matters -- nothing is CLAIMED.
    """
    from analysis.l5.e1_cohort import pair_lifecycle as PLC
    from analysis.l5.live_exec2 import orders as O

    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    oid = venue.rest("OURS-1", side=UP, wire="0.49", qty="0.13",
                     slug=wired["slug"])
    lc = PLC.PairLifecycle()
    lc.record_first_leg(order_id=oid, window=0, slot=0, price="0.49")
    dm = lc.deadman(snapshot={"market": wired["slug"], "window": 0,
                              "order_state": None},
                    now=clock.t,
                    transport_factory=cancelling_transport(venue),
                    orders_module=O, owned_ids=[oid])
    assert dm["ran"] is False
    assert dm["because"] == "ORDER_STATE_NOT_ESTABLISHED"
    assert dm["FAILS_CLOSED"] is True
    assert "all_cancelled" not in dm
    assert "cancels" not in dm
    assert venue.orders[oid]["state"] == "ORDER_STATE_NEW"


def test_a_failed_reread_falls_back_and_says_so(wired):
    """A RE-READ THAT FAILS IS RECORDED, NOT HIDDEN.

    Ownership comes from our own registry, so a stale view cannot make us
    touch a stranger's order, and every id is re-verified individually
    afterwards. So the handover proceeds from the last established view
    rather than abandoning resting orders -- and it reports that it did.
    """
    clock = Clock(BASE + 10)
    venue, prov = _wire(wired, clock, known=[], roles={},
                        pending=[], die_after=3)
    with pytest.raises(InjectedCrash):
        R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                     recorder=_Rec(),
                     transport_factory=cancelling_transport(venue),
                     now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                     progress=_Prog(), max_iterations=40)
    oid = venue.posted[0]
    prov.go_blind()
    rec = R.run_emergency_handover(cause="UNCAUGHT_EXCEPTION")
    assert rec["order_state_reread_failed"] == "OSError"
    assert rec["fell_back_to_last_established_view"] is True
    assert rec["deadman"]["targets"] == [oid]
    assert venue.orders[oid]["state"] == "ORDER_STATE_CANCELED"


def test_a_failed_cancel_is_a_violation_not_a_clean_shutdown(wired):
    """The venue accepts the cancel call and the order stays open. The
    handover's own verification -- never the cancel's answer -- decides."""
    clock = Clock(BASE + 10)
    known: List[str] = []
    roles: Dict[str, str] = {}
    venue, prov = _wire(wired, clock, known=known, roles=roles,
                        pending=[], die_after=3)

    class Deaf(cancelling_transport(venue)):       # type: ignore[misc]
        def request(self, method, path, body=None, base=None):
            if path.startswith("/v1/order/") and path.endswith("/cancel"):
                # a lying venue: 200 OK, nothing cancelled
                return {"canceledOrderIds": []}
            return super().request(method, path, body=body, base=base)

    tracker = SP.CohortTracker()
    with pytest.raises(InjectedCrash):
        R.run_cohort(snapshot_provider=prov, tracker=tracker, recorder=_Rec(),
                     transport_factory=Deaf, now_fn=clock,
                     sleep_fn=clock.sleep, pace_s=2.0,
                     progress=_Prog(), max_iterations=40)
    oid = venue.posted[0]
    rec = R.run_emergency_handover(cause="UNCAUGHT_EXCEPTION")
    dm = rec["deadman"]
    assert dm["ran"] is True
    assert dm["all_cancelled"] is False
    assert oid in dm["owned_still_open"]
    assert venue.orders[oid]["state"] == "ORDER_STATE_NEW"
    viol = [v for v in tracker.safety_violations
            if "SHUTDOWN_HANDOVER_DID_NOT_CLEAR" in str(v)]
    assert viol, tracker.snapshot()


def test_the_handover_never_replaces_the_original_failure():
    """A handover that itself blows up is reported, not propagated: losing
    the real incident to a secondary error makes it unreadable."""
    def _explode(_cause):
        raise MemoryError("no room to cancel")

    R._set_emergency_handover(_explode)
    rec = R.run_emergency_handover(cause="UNCAUGHT_EXCEPTION")
    assert rec["ran"] is False
    assert rec["because"] == "EMERGENCY_HANDOVER_RAISED:MemoryError"
    assert rec["FAILS_CLOSED"] is True
    assert "cancel_by_id" in rec["MEANING"]


# =====================================================================
# 4. main(): the orderly stop, and the abnormal one
# =====================================================================

class _StubProvider:
    """Only what `main` itself touches after construction. `run_cohort` is
    stubbed in these tests, so nothing reads a snapshot."""

    known_order_ids: List[str] = []
    order_roles: Dict[str, str] = {}
    pending_order_ids: List[str] = []
    terminal_order_cache: Dict[str, Any] = {}

    def attach_budget(self, *a, **k):
        return None

    def report(self):
        return {"stub": True}


def _main_with(monkeypatch, tmp_path, run_cohort_impl):
    """Drive the REAL `main`, with the REAL progress/auth/recorder.

    My first version passed `lambda d: None` for those factories and
    `main` died on `prog.rehydrate` before it ever reached the shutdown
    path -- the test would have reported a pass for the wrong reason had
    the assertion been weaker. The only thing stubbed is `run_cohort`
    itself, which is what the injection needs to control.
    """
    arm(monkeypatch)
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setattr(R, "run_cohort", run_cohort_impl)
    return R.main([], provider_factory=lambda **k: _StubProvider(),
                  progress_factory=lambda d: PG.CohortProgress(d),
                  auth_factory=lambda d: AUTH.SessionAuthorization(d),
                  recorder_factory=lambda d: RC.Recorder(
                      os.path.join(d, "tape")),
                  transport_factory=FakeTransport)


def test_main_runs_the_handover_on_sigterm_and_exits_nonzero(monkeypatch,
                                                             tmp_path):
    ran = []

    def _cohort(**kw):
        R._set_emergency_handover(lambda cause: ran.append(cause) or
                                  {"ran": True, "cause": cause})
        os.kill(os.getpid(), signal.SIGTERM)
        raise AssertionError("the signal must interrupt the cohort")

    rc = _main_with(monkeypatch, tmp_path, _cohort)
    assert rc == 4, "an interrupted probe must not read as a clean run"
    assert ran and ran[0].startswith("SHUTDOWN_SIGNAL:SIGTERM")


def test_main_runs_the_handover_on_an_uncaught_exception_and_reraises(
        monkeypatch, tmp_path):
    ran = []

    class Boom(RuntimeError):
        pass

    def _cohort(**kw):
        R._set_emergency_handover(lambda cause: ran.append(cause) or
                                  {"ran": True})
        raise Boom("venue storm")

    with pytest.raises(Boom):
        _main_with(monkeypatch, tmp_path, _cohort)
    assert ran == ["UNCAUGHT_EXCEPTION"]


def test_main_does_not_run_a_handover_when_the_cohort_cleared_it(monkeypatch,
                                                                 tmp_path):
    ran = []

    def _cohort(**kw):
        R._set_emergency_handover(lambda cause: ran.append(cause))
        R._set_emergency_handover(None)           # a normal return clears it
        return {"stopped_because": None}

    rc = _main_with(monkeypatch, tmp_path, _cohort)
    assert rc == 0 and ran == []


# =====================================================================
# 5. THE HARD CRASH: what survives when no handler runs at all
# =====================================================================

def test_a_simulated_hard_crash_leaves_the_latch_and_the_tape_behind(
        wired, tmp_path):
    """SIGKILL EQUIVALENT: no handover, no unwind, nothing cleared.

    The process is simply gone, so the test asserts on what is left ON
    DISK -- the O_EXCL latch slot written immediately before the POST, and
    the append-only tape -- because that, and not any handler, is what the
    next process has to work from.
    """
    clock = Clock(BASE + 10)
    known: List[str] = []
    roles: Dict[str, str] = {}
    venue, prov = _wire(wired, clock, known=known, roles=roles,
                        pending=[], die_after=3)
    rec = _Rec()

    with pytest.raises(InjectedCrash):
        R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                     recorder=rec,
                     transport_factory=cancelling_transport(venue),
                     now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                     progress=_Prog(), max_iterations=40)
    # THE PROCESS IS GONE. No handover ran.
    R._set_emergency_handover(None)
    oid = venue.posted[0]
    assert venue.orders[oid]["state"] == "ORDER_STATE_NEW", \
        "the premise: an order is resting and nothing cancelled it"

    # 1. the latch slot is on disk, fsynced, and carries the intent
    slots = [f for f in os.listdir(L.LATCH_DIR) if f.endswith(".submitted")]
    assert slots, os.listdir(L.LATCH_DIR)
    import json
    body = json.loads(open(os.path.join(L.LATCH_DIR, slots[0])).read())
    for k in ("slug", "side", "price", "qty", "window", "slot", "pid"):
        assert k in body, body
    assert body["NOT_RELEASED_ON_FAILURE"] is True

    # 2. the submitted id is recoverable from the tape, by role
    subs = [e for e in rec.events if e["event"] == "FIRST_LEG_SUBMITTED"]
    assert subs, rec.kinds()
    assert subs[0]["detail"]["order_id"] == oid


def test_a_restart_with_a_resting_order_refuses_to_submit(wired, tmp_path):
    """STARTUP RECONCILIATION, AFTER THE CRASH ABOVE.

    A new process comes up with an order of ours still resting at the
    venue. It must refuse BEFORE the first window, place nothing, and name
    why -- not inherit the order silently and not cancel it either, since
    cancelling is the operator's call once ownership is in doubt.
    """
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    oid = venue.rest("CRASHED-1", side=UP, wire="0.49", qty="0.13",
                     slug=wired["slug"])
    known = [oid]
    roles = {oid: OS.FIRST_LEG}
    prov = Wrapped(_provider(venue, clock, pin=wired, known=known,
                             roles=roles, pending=[]), venue)
    FakeTransport.posts = []
    out = R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                       recorder=_Rec(),
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=40)
    assert out["stopped_because"] == "STARTUP_RECONCILIATION_REFUSED"
    assert out["startup_reconciliation"]["may_submit"] is False
    assert out["placement_count"] == 0
    assert out["latch_slots_consumed"] == 0
    assert FakeTransport.posts == [], "a refusal must place nothing"
    # and it did NOT cancel the stranger's-or-ours order on its own
    assert venue.orders[oid]["state"] == "ORDER_STATE_NEW"


def test_an_unreadable_venue_on_restart_also_refuses(wired):
    """FAIL CLOSED, not fail open: a restart that cannot read order state
    must not treat that as "no orders"."""
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    prov = Wrapped(_provider(venue, clock, pin=wired, known=[], roles={},
                             pending=[]), venue)

    prov.go_blind()
    FakeTransport.posts = []
    out = R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                       recorder=_Rec(),
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=40)
    assert out["stopped_because"] == "STARTUP_RECONCILIATION_REFUSED"
    sr = out["startup_reconciliation"]
    assert sr["may_submit"] is False
    assert sr["FAILS_CLOSED"] is True
    assert "STARTUP_RECONCILIATION_RAISED:OSError" in sr["because"]
    assert FakeTransport.posts == []

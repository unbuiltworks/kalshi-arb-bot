"""ONE ATTEMPT, ONE WINDOW -- PROVEN THROUGH THE PRODUCTION ENTRY POINT.

WHAT THESE TESTS ARE FOR. The owner authorized ONE bounded live probe. The
frozen spec authorizes twenty attempts in each of twelve windows, the
deployed `main()` read no argument, and the only deployment-side lever left
was pre-creating latch files on the volume -- which records a false pending
intent, sends a real preview, and then raises an uncaught `WindowExhausted`
that would abandon a filled residual. So the limit became authorization:
`PMUS_E1_MAX_ATTEMPTS` and `PMUS_E1_MAX_WINDOWS`, narrowing only.

EVERY LIFECYCLE TEST HERE DRIVES `run_cohort` OR `main`. None calls
`submit_first_leg`, `submit_complement`, the lifecycle or the venue
directly, for the reason `test_e1_run_cohort_lifecycle.py` exists: a helper
that works proves nothing about whether the engine reaches it. The simulated
venue fills and cancels on its own schedule, so the sequences asserted below
are the loop's behaviour and not the test's script.

THE PROPERTY THAT MATTERS MOST is the one that is easiest to get wrong:
exhausting the attempt budget must stop NEW FIRST LEGS and must NOT stop
managing a first leg that is filled, partially filled, or possibly filled.
An attempt bound whose enforcement strands inventory is worse than no bound.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List

import pytest

from analysis.l5.e1_cohort import budget as BUD
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import spec as SPEC
from analysis.l5.e1_cohort import stopping as ST
from analysis.l5.e1_cohort.test_e1_live_path import (FakeTransport, arm,
                                                     fake_pin, BASE)
from analysis.l5.e1_cohort.test_e1_pair_lifecycle import UP, DOWN
from analysis.l5.e1_cohort.test_e1_run_cohort_lifecycle import (
    Clock, LoopVenue, cancelling_transport, _provider, _Prog, _Rec)
from analysis.l5.live_exec2 import orders as O

W = 900
SID = "e1-live-test-session"


def one_probe(*, windows_used_at_start=0):
    """EXACTLY the owner's authorization: one attempt, one window."""
    return BUD.ProbeBudget(max_attempts=1, max_windows=1,
                           windows_used_at_start=windows_used_at_start,
                           source="test:one_bounded_probe")


class CountingTransport(object):
    """Wraps a transport factory and counts PREVIEWS as well as POSTs.

    The preview matters independently: the owner's requirement is that no
    SECOND first-leg intent, preview or POST occurs, and a preview is a real
    request to the venue even though it creates no order.
    """

    previews: List[str] = []

    def __init__(self, inner):
        self.inner = inner

    def __call__(self, *a, **k):
        t = self.inner(*a, **k)
        outer = self

        class T(object):
            def __getattr__(self, name):
                return getattr(t, name)

            def request(self, method, path, body=None, base=None):
                if path == O.PREVIEW_PATH:
                    outer.previews.append(str((body or {}).get("request", {})
                                              .get("marketSlug")))
                return t.request(method, path, body=body, base=base)

        return T()


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    FakeTransport.posts = []
    CountingTransport.previews = []
    return fake_pin(BASE)


def drive(pin, *, fill_plan=(), budget=None, cutoff_at=BASE + 60,
          max_iterations=200, known=None, roles=None, pending=None,
          clock_at=BASE + 10, comp_bid="0.47"):
    """Run the PRODUCTION loop once, with a venue on its own schedule."""
    clock = Clock(clock_at)
    venue = LoopVenue(clock, pin["slug"])
    venue.fill_plan = [dict(f) for f in fill_plan]
    known = [] if known is None else known
    roles = {} if roles is None else roles
    pending = [] if pending is None else pending
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending, cutoff_at=cutoff_at,
                     comp_bid=comp_bid)
    rec = _Rec()
    prog = _Prog()
    tracker = ST.CohortTracker()
    tf = CountingTransport(cancelling_transport(venue))
    out = R.run_cohort(snapshot_provider=prov, tracker=tracker, recorder=rec,
                       transport_factory=tf, now_fn=clock,
                       sleep_fn=clock.sleep, pace_s=2.0, progress=prog,
                       max_iterations=max_iterations,
                       budget=budget if budget is not None else one_probe())
    return {"out": out, "rec": rec, "prog": prog, "tracker": tracker,
            "venue": venue, "clock": clock, "known": known, "roles": roles,
            "pending": pending, "posts": list(FakeTransport.posts),
            "previews": list(CountingTransport.previews)}


def first_leg_posts(run):
    """POSTs whose registered role is FIRST_LEG. Counted from the REAL role
    registry, never from the post index."""
    return [p for p in run["posts"]
            if str(run["roles"].get("order-%d" % p["n"])) != OS.COMPLEMENT]


# =====================================================================
# 1. THE AUTHORIZATION VALUES THEMSELVES: narrowing only, fail closed
# =====================================================================

@pytest.mark.parametrize("value", [None, "", "   ", "one", "1.0", "0x1",
                                   "-1", "0", "241", "999999", "1e3",
                                   "+1", " 1 2 "])
def test_a_bad_or_excessive_attempt_limit_is_REFUSED(monkeypatch, value):
    monkeypatch.setenv(BUD.MAX_WINDOWS_ENV, "1")
    if value is None:
        monkeypatch.delenv(BUD.MAX_ATTEMPTS_ENV, raising=False)
    else:
        monkeypatch.setenv(BUD.MAX_ATTEMPTS_ENV, value)
    with pytest.raises(BUD.BudgetNotAuthorized):
        BUD.ProbeBudget.from_env()


@pytest.mark.parametrize("value", [None, "", "no", "0", "-3", "13", "99"])
def test_a_bad_or_excessive_window_limit_is_REFUSED(monkeypatch, value):
    monkeypatch.setenv(BUD.MAX_ATTEMPTS_ENV, "1")
    if value is None:
        monkeypatch.delenv(BUD.MAX_WINDOWS_ENV, raising=False)
    else:
        monkeypatch.setenv(BUD.MAX_WINDOWS_ENV, value)
    with pytest.raises(BUD.BudgetNotAuthorized):
        BUD.ProbeBudget.from_env()


def test_the_limits_may_narrow_and_can_never_widen(monkeypatch):
    monkeypatch.setenv(BUD.MAX_ATTEMPTS_ENV, "1")
    monkeypatch.setenv(BUD.MAX_WINDOWS_ENV, "1")
    b = BUD.ProbeBudget.from_env()
    assert (b.max_attempts, b.max_windows) == (1, 1)
    # the frozen maximum itself is accepted: narrowing by zero is allowed
    monkeypatch.setenv(BUD.MAX_ATTEMPTS_ENV, str(BUD.FROZEN_MAX_ATTEMPTS))
    monkeypatch.setenv(BUD.MAX_WINDOWS_ENV, str(BUD.FROZEN_MAX_WINDOWS))
    b = BUD.ProbeBudget.from_env()
    assert b.max_attempts == BUD.FROZEN_MAX_ATTEMPTS == \
        SPEC.WINDOWS * SPEC.MAX_ATTEMPTS_PER_WINDOW
    # one past it is not
    monkeypatch.setenv(BUD.MAX_ATTEMPTS_ENV,
                       str(BUD.FROZEN_MAX_ATTEMPTS + 1))
    with pytest.raises(BUD.BudgetNotAuthorized):
        BUD.ProbeBudget.from_env()


def test_the_attempt_count_comes_from_DURABLE_LATCH_FILES(monkeypatch,
                                                          tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setenv("PMUS_E1_SESSION_ID", SID)
    b = one_probe()
    assert b.attempt_gate()["may_attempt"] is True
    # consume one slot exactly as the pre-POST latch does
    L.consume_attempt(window=0, slug="s", side=UP, price="0.49", qty="0.13")
    g = b.attempt_gate()
    assert g["may_attempt"] is False
    assert g["usage"]["latch_slots_spent"] == 1
    assert "ATTEMPT_BUDGET_EXHAUSTED_1_OF_1" in g["because"]
    # A FRESH PROCESS -- a brand new budget object, nothing in memory --
    # reads the same answer off the volume. This is what makes a restart
    # unable to buy a second attempt.
    assert one_probe().attempt_gate()["may_attempt"] is False


def test_an_unretired_local_intent_counts_as_a_spent_attempt(monkeypatch,
                                                             tmp_path):
    """THE AMBIGUOUS CASE. A POST whose acknowledgement never arrived may
    have rested a real order, so the attempt is spent."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setenv("PMUS_E1_SESSION_ID", SID)
    g = one_probe().attempt_gate(
        pending_order_ids=[OS.LOCAL_INTENT + "w0:slug:UP:0.49"])
    assert g["may_attempt"] is False
    assert g["usage"]["unretired_local_intents"] == 1
    assert g["usage"]["ambiguous_submission_counts_as_spent"] is True


def test_an_unreadable_latch_fails_CLOSED(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setenv("PMUS_E1_SESSION_ID", SID)

    class Broken(object):
        @staticmethod
        def cohort_state(sid=None):
            raise OSError("volume gone")

    g = one_probe().attempt_gate(latch_module=Broken)
    assert g["may_attempt"] is False
    assert g["usage"]["latch_readable"] is False
    assert "LATCH_UNREADABLE" in g["usage"]["why"]


def test_the_window_gate_counts_NEW_windows_only(monkeypatch):
    """The cohort ledger already holds spent windows and is not rewritten:
    the probe's one window is counted on top of whatever is there."""
    b = one_probe(windows_used_at_start=3)
    assert b.window_gate(windows_used=3)["may_open_window"] is True
    g = b.window_gate(windows_used=4)
    assert g["may_open_window"] is False
    assert g["because"] == "PROBE_WINDOW_BUDGET_SPENT_1_OF_1"
    assert g["ledger_is_read_never_rewritten"] is True
    # and the FROZEN ceiling still names itself when it is the binding one
    wide = BUD.ProbeBudget(max_attempts=1,
                           max_windows=BUD.FROZEN_MAX_WINDOWS,
                           windows_used_at_start=0, source="t")
    assert wide.window_gate(
        windows_used=SPEC.WINDOWS)["because"] == "COHORT_WINDOW_BUDGET_SPENT"


# =====================================================================
# 2. THE DEPLOYED ENTRY POINT REFUSES WITHOUT A BUDGET
# =====================================================================

def _main_with(monkeypatch, capsys, **env):
    arm(monkeypatch)
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    built: List[str] = []

    def provider_factory(**k):
        built.append("provider")
        raise AssertionError("no provider may be constructed")

    def progress_factory(d):
        built.append("progress")
        raise AssertionError("no progress store may be opened")

    rc = R.main(provider_factory=provider_factory,
                progress_factory=progress_factory)
    return rc, capsys.readouterr().out, built


def test_main_REFUSES_when_the_attempt_limit_is_missing(monkeypatch,
                                                       capsys):
    rc, out, built = _main_with(monkeypatch, capsys,
                                PMUS_E1_MAX_ATTEMPTS=None,
                                PMUS_E1_MAX_WINDOWS="1")
    assert rc == 2
    assert "PROBE_BUDGET_NOT_AUTHORIZED" in out
    # BEFORE any provider, feed, window, latch or order path exists.
    assert built == []
    assert '"no_window_opened": true' in out
    assert '"no_latch_slot_claimed": true' in out
    assert '"no_order_endpoint_reached": true' in out


@pytest.mark.parametrize("attempts,windows", [("0", "1"), ("1", "0"),
                                              ("x", "1"), ("1", "x"),
                                              ("241", "1"), ("1", "13"),
                                              ("", ""), ("1", None)])
def test_main_REFUSES_every_invalid_or_excessive_budget(monkeypatch, capsys,
                                                        attempts, windows):
    rc, out, built = _main_with(monkeypatch, capsys,
                                PMUS_E1_MAX_ATTEMPTS=attempts,
                                PMUS_E1_MAX_WINDOWS=windows)
    assert rc == 2
    assert "PROBE_BUDGET_NOT_AUTHORIZED" in out
    assert built == []


def test_main_reports_the_budget_once_it_is_authorized(monkeypatch, capsys,
                                                      tmp_path):
    """The armed path gets as far as building the provider ONLY with a
    valid narrowing budget, and prints exactly what it was narrowed to."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    monkeypatch.setenv(BUD.MAX_ATTEMPTS_ENV, "1")
    monkeypatch.setenv(BUD.MAX_WINDOWS_ENV, "1")
    seen: Dict[str, Any] = {}

    def provider_factory(*, progress, session_auth, recorder, budget=None):
        seen["budget"] = budget
        raise SystemExit(0)

    with pytest.raises(SystemExit):
        R.main(provider_factory=provider_factory)
    assert seen["budget"].max_attempts == 1
    assert seen["budget"].max_windows == 1
    out = capsys.readouterr().out
    assert '"max_attempts": 1' in out
    assert '"max_windows": 1' in out


# =====================================================================
# 3. ONE UNFILLED FIRST LEG, THEN A CLEAN STOP
# =====================================================================

def test_one_unfilled_first_leg_then_handover_then_clean_stop(wired):
    run = drive(wired)
    out = run["out"]
    # EXACTLY ONE first-leg attempt: one POST, one preview, one intent.
    assert len(first_leg_posts(run)) == 1, run["posts"]
    assert len(run["previews"]) == 1, run["previews"]
    assert run["rec"].kinds().count("FIRST_LEG_SUBMISSION_INTENT") == 1
    assert run["rec"].kinds().count("FIRST_LEG_SUBMITTED") == 1
    # the budget was reached and said so, exactly once
    assert out["budget_events"], out["budget_events"]
    assert run["rec"].kinds().count("ATTEMPT_BUDGET_EXHAUSTED") == 1
    # the loop did NOT stop at the attempt: it stayed to cancel the order
    assert "deadman" in json.dumps(out["lifecycle"]["events"])
    dm = [e["deadman"] for e in out["lifecycle"]["events"]
          if e.get("deadman")]
    assert dm and dm[0]["all_cancelled"] is True
    assert dm[0]["CANCELS_BY_OWNED_ORDER_ID_NOT_BY_MARKET"] is True
    # and then stopped cleanly, with no violation
    assert out["stopped_because"].startswith("ATTEMPT_BUDGET_EXHAUSTED"), \
        out["stopped_because"]
    assert run["tracker"].snapshot()["safety_violations"] == []


def test_no_second_first_leg_intent_preview_or_post_ever_occurs(wired):
    """THE OWNER'S EXPLICIT CHECK. Run long, with a book that stays
    eligible the whole time, and count."""
    run = drive(wired, cutoff_at=BASE + 400, max_iterations=400)
    assert len(first_leg_posts(run)) == 1
    assert len(run["previews"]) == 1
    assert run["rec"].kinds().count("FIRST_LEG_SUBMISSION_INTENT") == 1
    # no latch slot beyond the first was ever consumed, on the VOLUME
    assert L.cohort_state(SID)["attempts_spent_total"] == 1


# =====================================================================
# 4. A PARTIAL FILL IS STILL MANAGED AFTER THE BUDGET IS SPENT
# =====================================================================

def test_a_partial_fill_is_complemented_after_the_budget_is_exhausted(
        wired):
    run = drive(wired, cutoff_at=BASE + 200, max_iterations=300,
                fill_plan=[
                    {"at": BASE + 16, "qty": "0.05",
                     "match": lambda oid, o: o["outcomeSide"] == UP},
                    {"at": BASE + 40, "qty": None, "repeat": True,
                     "match": lambda oid, o: o["outcomeSide"] == DOWN},
                ])
    kinds = run["rec"].kinds()
    # the attempt budget was spent BEFORE the complement was submitted
    assert "ATTEMPT_BUDGET_EXHAUSTED" in kinds
    assert kinds.index("ATTEMPT_BUDGET_EXHAUSTED") < \
        kinds.index("COMPLEMENT_SUBMITTED"), kinds
    assert "FIRST_LEG_FILL_RESIDUAL_OPENED" in kinds
    assert run["out"]["lifecycle"][
        "complement_is_reached_from_run_cohort"] is True
    # still exactly one first leg
    assert len(first_leg_posts(run)) == 1
    # the complement never exceeded what the leg actually filled
    comps = [c for e in run["out"]["lifecycle"]["events"]
             for c in (e.get("complements") or []) if c.get("submitted")]
    assert comps
    for c in comps:
        assert D(str(c["qty"])) <= D("0.05") + D("0.08")


def test_attempt_exhaustion_while_a_residual_is_open_does_not_stop_early(
        wired):
    """The loop must not treat 'no attempts left' as 'nothing left to do'
    while inventory exists. It stops at the HANDOVER, not before it."""
    run = drive(wired, cutoff_at=BASE + 300, max_iterations=400,
                fill_plan=[{"at": BASE + 16, "qty": "0.13",
                            "match": lambda oid, o: o["outcomeSide"] == UP}])
    out = run["out"]
    assert out["stopped_because"] == "ATTEMPT_BUDGET_EXHAUSTED_AT_HANDOVER"
    assert run["tracker"].unresolved_residual_max_loss_usd() > 0
    # it kept working: the complement was submitted after exhaustion
    assert "COMPLEMENT_SUBMITTED" in run["rec"].kinds()
    # and the filled residual was NOT unwound
    dm = [e["deadman"] for e in out["lifecycle"]["events"]
          if e.get("deadman")]
    assert dm and dm[0]["filled_residual_rides_to_settlement"] is True
    assert dm[0]["NOT_A_TAKER_RESCUE"] is True


# =====================================================================
# 5. FILLED FIRST LEG, UNFILLED COMPLEMENT -> EXACT-ID DEADMAN
# =====================================================================

def test_a_filled_leg_with_an_unfilled_complement_is_cancelled_by_id(wired):
    run = drive(wired, cutoff_at=BASE + 120, max_iterations=300,
                fill_plan=[{"at": BASE + 16, "qty": "0.13",
                            "match": lambda oid, o: o["outcomeSide"] == UP}])
    out = run["out"]
    dm = [e["deadman"] for e in out["lifecycle"]["events"]
          if e.get("deadman")]
    assert dm, out["lifecycle"]["events"]
    d = dm[0]
    assert d["all_cancelled"] is True
    assert d["foreign_untouched"] is True
    assert d["VERIFIED_AGAINST"] == "GET /v1/order/{id} per owned id"
    # every cancelled id was one of OURS
    assert set(d["targets"]) <= set(run["known"])
    # the complement that was resting is terminal at the venue now
    for oid in d["targets"]:
        assert D(run["venue"].orders[oid]["leavesQuantity"]) == 0
    assert run["tracker"].snapshot()["safety_violations"] == []


def test_a_foreign_order_in_the_SAME_market_is_never_cancelled(wired):
    """The shared-account safety property, under the one-probe budget."""
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    venue.rest("someone-elses-order", side=DOWN, wire="0.40", qty="16.21",
               slug=wired["slug"])
    known: List[str] = []
    roles: Dict[str, str] = {}
    prov = _provider(venue, clock, pin=wired, known=known, roles=roles,
                     pending=[], cutoff_at=BASE + 120)
    out = R.run_cohort(snapshot_provider=prov, tracker=ST.CohortTracker(),
                       recorder=_Rec(),
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=300,
                       budget=one_probe())
    dm = [e["deadman"] for e in out["lifecycle"]["events"]
          if e.get("deadman")]
    if dm:
        assert "someone-elses-order" not in dm[0]["targets"]
    assert D(venue.orders["someone-elses-order"]["leavesQuantity"]) > 0


# =====================================================================
# 6. AMBIGUOUS SUBMISSION, THEN A RESTART
# =====================================================================

def test_an_ambiguous_submission_spends_the_only_attempt(wired, monkeypatch):
    """The POST's answer never arrives. The attempt is spent, the cohort
    stops to reconcile, and the DURABLE record says so."""
    class Ambiguous(FakeTransport):
        def request(self, method, path, body=None, base=None):
            if path == O.SUBMIT_PATH:
                raise O.SubmissionAmbiguous("504", detail={"status": 504})
            return super().request(method, path, body=body, base=base)

    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    pending: List[str] = []
    prov = _provider(venue, clock, pin=wired, known=[], roles={},
                     pending=pending, cutoff_at=BASE + 300)
    rec = _Rec()
    out = R.run_cohort(snapshot_provider=prov, tracker=ST.CohortTracker(),
                       recorder=rec, transport_factory=Ambiguous,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=50,
                       budget=one_probe())
    assert out["stopped_because"] == \
        "AMBIGUOUS_SUBMISSION_RECONCILE_VENUE_STATE"
    assert "AMBIGUOUS_SUBMISSION" in rec.kinds()
    # THE LATCH SLOT IS SPENT, because it is taken before the POST and
    # never released -- so the one authorized attempt is gone.
    assert L.cohort_state(SID)["attempts_spent_total"] == 1
    # and the unretired intent is still blocking
    assert [p for p in pending if p.startswith(OS.LOCAL_INTENT)]
    assert one_probe().attempt_gate(
        pending_order_ids=pending)["may_attempt"] is False


def test_a_RESTART_after_an_ambiguous_submission_places_nothing(wired):
    """SECOND PROCESS, SAME VOLUME. The latch slot and the tape are what
    survive, and they are enough to refuse."""
    # process 1: ambiguous
    class Ambiguous(FakeTransport):
        def request(self, method, path, body=None, base=None):
            if path == O.SUBMIT_PATH:
                raise O.SubmissionAmbiguous("504", detail={"status": 504})
            return super().request(method, path, body=body, base=base)

    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    prov = _provider(venue, clock, pin=wired, known=[], roles={},
                     pending=[], cutoff_at=BASE + 300)
    R.run_cohort(snapshot_provider=prov, tracker=ST.CohortTracker(),
                 recorder=_Rec(), transport_factory=Ambiguous, now_fn=clock,
                 sleep_fn=clock.sleep, pace_s=2.0, progress=_Prog(),
                 max_iterations=50, budget=one_probe())
    assert L.cohort_state(SID)["attempts_spent_total"] == 1

    # process 2: a fresh everything. Nothing in memory carries over.
    FakeTransport.posts = []
    CountingTransport.previews = []
    run = drive(wired, cutoff_at=BASE + 200, max_iterations=200)
    assert first_leg_posts(run) == [], run["posts"]
    assert run["previews"] == [], run["previews"]
    assert run["rec"].kinds().count("FIRST_LEG_SUBMISSION_INTENT") == 0
    assert L.cohort_state(SID)["attempts_spent_total"] == 1
    assert run["out"]["stopped_because"].startswith(
        "ATTEMPT_BUDGET_EXHAUSTED")


# =====================================================================
# 7. LATCH EXHAUSTION: NO CRASH, NO FALSE INTENT, NO LOST INVENTORY
# =====================================================================

def test_a_pre_exhausted_latch_does_not_crash_and_writes_no_false_intent(
        wired):
    """THE OLD DEFECT, DIRECTLY. Every slot in the window is spent before
    the loop starts. Previously: the intent row went on the tape, a real
    preview was sent, and `WindowExhausted` propagated out of `main()`
    uncaught -- abandoning anything that had filled."""
    for i in range(SPEC.MAX_ATTEMPTS_PER_WINDOW):
        L.consume_attempt(window=0, slug=wired["slug"], side=UP,
                          price="0.49", qty="0.13")
    assert L.window_state(window=0, session_id=SID)["exhausted"] is True

    wide = BUD.ProbeBudget(max_attempts=BUD.FROZEN_MAX_ATTEMPTS,
                           max_windows=1, windows_used_at_start=0,
                           source="test:budget_is_not_the_binding_limit")
    run = drive(wired, budget=wide, cutoff_at=BASE + 120,
                max_iterations=200)
    # no crash: we got a report back at all
    assert run["out"]["stopped_because"]
    # NOTHING was posted and NO preview was sent
    assert run["posts"] == []
    assert run["previews"] == []
    # and NO false pending intent was left anywhere
    assert run["rec"].kinds().count("FIRST_LEG_SUBMISSION_INTENT") == 0
    assert [p for p in run["pending"]
            if p.startswith(OS.LOCAL_INTENT)] == []


def test_the_latch_race_retires_its_intent_and_raises_no_RuntimeError(
        wired, monkeypatch):
    """The narrow case the read-only pre-check cannot cover: the slot is
    taken between the check and the O_EXCL create. The create fails BEFORE
    the POST, so the intent must be retired rather than left to block."""
    def racing(**k):
        raise L.WindowExhausted("lost the race")

    monkeypatch.setattr(L, "consume_attempt", racing)
    pending: List[str] = []
    rec = _Rec()
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    prov = _provider(venue, clock, pin=wired, known=[], roles={},
                     pending=pending, cutoff_at=BASE + 60)
    out = R.run_cohort(snapshot_provider=prov, tracker=ST.CohortTracker(),
                       recorder=rec,
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=60,
                       budget=one_probe())
    assert out["stopped_because"]                      # no exception escaped
    assert "FIRST_LEG_INTENT_RETIRED_LATCH_EXHAUSTED" in rec.kinds()
    assert [p for p in pending if p.startswith(OS.LOCAL_INTENT)] == []


def test_a_cancel_fill_race_at_handover_keeps_the_fill_and_adds_no_leg(
        wired):
    """THE RACE. The venue fills the resting order in the same instant the
    handover cancels it. The fill is real inventory and must survive; the
    cancel must not be reported as having cleared something it did not; and
    the one-attempt budget must still forbid a replacement leg."""
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    known: List[str] = []
    roles: Dict[str, str] = {}
    prov = _provider(venue, clock, pin=wired, known=known, roles=roles,
                     pending=[], cutoff_at=BASE + 60)

    class Racing(object):
        """A cancel that FILLS the order instead of cancelling it."""

        def __init__(self, inner):
            self.inner = inner

        def __call__(self, *a, **k):
            t = self.inner(*a, **k)

            class T(object):
                def __getattr__(self, n):
                    return getattr(t, n)

                def request(self, method, path, body=None, base=None):
                    if path.startswith("/v1/order/") \
                            and path.endswith("/cancel"):
                        oid = path.split("/")[3]
                        o = venue.orders.get(oid)
                        if o and D(o["leavesQuantity"]) > 0:
                            # the fill wins the race
                            venue.fill(oid, o["leavesQuantity"])
                            return {"canceledOrderIds": []}
                    return t.request(method, path, body=body, base=base)

            return T()

    tracker = ST.CohortTracker()
    rec = _Rec()
    out = R.run_cohort(snapshot_provider=prov, tracker=tracker, recorder=rec,
                       transport_factory=Racing(cancelling_transport(venue)),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=300,
                       budget=one_probe())
    # THE FILL SURVIVED. Nothing unwound it, at any price.
    filled = [o for o in venue.orders.values()
              if D(o["cumQuantity"]) > 0]
    assert filled, venue.orders
    # the loop did NOT place a replacement first leg
    assert len([p for p in FakeTransport.posts
                if str(roles.get("order-%d" % p["n"])) != OS.COMPLEMENT]) \
        == 1
    assert L.cohort_state(SID)["attempts_spent_total"] == 1
    # and the engine was HONEST about the cancel that did not cancel: a
    # deadman that cannot clear our own resting order is a violation, not
    # a shrug. Either it cleared (because the fill made it terminal) or it
    # escalated.
    dm = [e["deadman"] for e in out["lifecycle"]["events"]
          if e.get("deadman")]
    if dm and dm[0].get("all_cancelled") is False:
        assert any("HANDOVER_DEADMAN_DID_NOT_CLEAR" in str(v)
                   for v in tracker.snapshot()["safety_violations"]), \
            tracker.snapshot()["safety_violations"]


# =====================================================================
# 8. NO SECOND WINDOW
# =====================================================================

def test_the_provider_refuses_a_second_window_and_the_loop_HARD_STOPS(
        monkeypatch, tmp_path):
    """Through the real provider and the real append-only ledger."""
    from analysis.l5.e1_cohort import progress as PG
    from analysis.l5.e1_cohort import authorization as AU
    from analysis.l5.e1_cohort.test_e1_live_path import _provider as live_p

    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    p = live_p(tmp_path, "probe-window", clock=clock,
               starts=[BASE + i * W for i in range(4)])
    p.budget = one_probe(windows_used_at_start=p.progress.windows_used())
    assert p() is not None                     # the ONE authorized window
    assert p.progress.windows_used() == 1
    # roll past its end: the provider must NOT open another
    clock.t = BASE + W + 10
    assert p() is None
    assert p.window_budget_spent is True
    assert any(str(r["refused"]).startswith("PROBE_WINDOW_BUDGET_SPENT")
               for r in p.refusals), p.refusals
    assert p.progress.windows_used() == 1, "no second window was consumed"


def test_run_cohort_stops_at_once_when_the_window_budget_is_spent():
    """An empty snapshot is normally PACED, because a book gap is not the
    end of a cohort. A spent window budget is not a gap: it is the end of
    the authorized probe, and it must not be waited out."""
    class P(object):
        window_budget_spent = True
        known_order_ids: List[str] = []
        order_roles: Dict[str, str] = {}
        pending_order_ids: List[str] = []

        def __call__(self):
            return None

    slept: List[float] = []
    out = R.run_cohort(snapshot_provider=P(), tracker=ST.CohortTracker(),
                       now_fn=lambda: BASE, sleep_fn=slept.append,
                       pace_s=2.0, max_iterations=50,
                       budget=one_probe())
    assert out["stopped_because"] == "PROBE_WINDOW_BUDGET_SPENT"
    assert slept == [], "a spent window budget must not be paced"
    assert out["iterations"] == 1


# =====================================================================
# 9. THE FROZEN ECONOMICS ARE UNTOUCHED BY ANY OF THIS
# =====================================================================

def test_the_budget_changes_no_price_size_fee_or_risk_rule():
    cb = SPEC.SPEC["cohort_bounds"]
    assert cb["first_leg_qty_shares"] == "0.13"
    assert cb["max_first_leg_price_usd"] == "0.60"
    assert cb["pair_cost_ceiling_usd"] == "0.96"
    assert cb["pair_cost_ceiling_is_fee_inclusive"] is True
    assert cb["max_exposure_per_window_usd"] == "1.56"
    assert cb["authorized_window_ceiling_usd"] == "5.00"
    assert cb["max_attempts_per_window"] == 20
    assert cb["windows"] == 12
    pb = SPEC.SPEC["probe_budget"]
    assert pb["pricing_strategy_economics_or_risk_ceilings_changed"] is False
    assert pb["may_only_narrow_the_frozen_maximums"] is True
    assert pb["complement_management_continues_after_exhaustion"] is True
    assert pb["owned_order_cancellation_continues_after_exhaustion"] is True
    assert pb["window_exhausted_propagated_uncaught"] is False


def test_the_one_probe_posts_the_frozen_size_and_is_maker_only(wired):
    run = drive(wired)
    p = first_leg_posts(run)[0]
    assert str(p["quantity"]) == "0.13"
    assert p["post_only"] is True
    assert D(str(p["price"])) <= D("0.60")


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))

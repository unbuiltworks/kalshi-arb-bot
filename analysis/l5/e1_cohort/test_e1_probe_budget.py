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
import time as time_mod
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
    FakeTransport.cancels = []
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
    assert b.attempt_gate(window=0)["may_attempt"] is True
    # consume one slot exactly as the pre-POST latch does
    L.consume_attempt(window=0, slug="s", side=UP, price="0.49", qty="0.13")
    g = b.attempt_gate(window=0)
    assert g["may_attempt"] is False
    assert g["usage"]["latch_slots_spent"] == 1
    assert "ATTEMPT_BUDGET_EXHAUSTED_1_OF_1" in g["because"]
    # A FRESH PROCESS -- a brand new budget object, nothing in memory --
    # reads the same answer off the volume. This is what makes a restart
    # unable to buy a second attempt.
    assert one_probe().attempt_gate(window=0)["may_attempt"] is False
    # AND THE SESSION'S HISTORY IS NOT WHAT WAS COUNTED. Another window of
    # the same session carries its own slots and is not this probe's scope:
    # this is what lets MAX_ATTEMPTS=1 mean one NEW leg on a session that
    # already holds forty.
    for _ in range(20):
        L.consume_attempt(window=5, slug="s", side=UP, price="0.49",
                          qty="0.13")
    g2 = one_probe().attempt_gate(window=1)
    assert g2["may_attempt"] is True, g2
    assert g2["usage"]["counted_window"] == 1
    assert g2["usage"]["session_history_is_read_never_recounted"] is True


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

        @staticmethod
        def window_state(**k):
            raise OSError("volume gone")

    g = one_probe().attempt_gate(window=0, latch_module=Broken)
    assert g["may_attempt"] is False
    assert g["usage"]["latch_readable"] is False
    assert "LATCH_UNREADABLE" in g["usage"]["why"]


def test_the_window_gate_counts_NEW_windows_only(monkeypatch, tmp_path):
    """The cohort ledger already holds spent windows and is not rewritten:
    the probe's one window is counted on top of whatever is there."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setenv("PMUS_E1_SESSION_ID", SID)
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
    assert '"no_window_opened":true' in out
    assert '"no_latch_slot_claimed":true' in out
    assert '"no_order_endpoint_reached":true' in out


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
    assert '"max_attempts":1' in out
    assert '"max_windows":1' in out


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
    FakeTransport.cancels = []
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
               or str(r["refused"]).startswith("PROBE_IS_BOUND_TO_WINDOW")
               for r in p.refusals), p.refusals
    assert p.progress.windows_used() == 1, "no second window was consumed"
    # AND THE BINDING EXISTS, naming the one authorized window identity.
    bound = BUD.read_binding()
    assert bound is not None and bound["window"] == 0, bound
    assert bound["IMMUTABLE"] is True and bound["NEVER_RELEASED"] is True


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


# =====================================================================
# 10. THE PRODUCTION ENTRY POINT, AND RESTARTS THROUGH IT
# =====================================================================
#
# Everything above drives `run_cohort`. These drive `runner.main()` itself,
# twice, on ONE state directory -- which is what a restart actually is. The
# binding, the latch slots, the progress ledger and the tape are the only
# things that carry over, and they have to be enough.

def _probe_env(monkeypatch, sid):
    arm(monkeypatch, sid)
    monkeypatch.setenv(BUD.MAX_ATTEMPTS_ENV, "1")
    monkeypatch.setenv(BUD.MAX_WINDOWS_ENV, "1")


def _venue_transport(venue, *, submit_raises=None):
    """The cancelling transport, plus the venue actually RESTING what the
    production path posts. Nothing mirrors posts for us here: the real
    provider is in play, so the venue has to learn about an order the same
    way it would in production -- from the POST itself."""
    inner = cancelling_transport(venue)

    class T(inner):                                            # type: ignore
        def request(self, method, path, body=None, base=None):
            if path == O.SUBMIT_PATH and submit_raises is not None:
                raise submit_raises
            out = super().request(method, path, body=body, base=base)
            if path == O.SUBMIT_PATH and isinstance(out, dict) \
                    and out.get("id"):
                venue.on_post(str(out["id"]),
                              side=body["outcomeSide"],
                              wire=str(body["price"]["value"]),
                              qty=str(body["quantity"]))
            return out

    return T


def _pumping_reader(venue):
    inner = venue.reader()

    def read(path):
        venue.pump()
        return inner(path)

    return read


def _run_main(monkeypatch, state_dir, *, venue, clock, sid, starts,
              submit_raises=None, cutoff_window_end=None):
    """ONE invocation of the REAL production entry point."""
    from analysis.l5.e1_cohort import authorization as AU
    from analysis.l5.e1_cohort import progress as PG
    from analysis.l5.e1_cohort import pmus_provider as PV
    from analysis.l5.e1_cohort import recorder as RC
    from analysis.l5.e1_cohort.test_e1_live_path import (FakeFeed,
                                                        fake_selector)

    _probe_env(monkeypatch, sid)
    monkeypatch.setattr(L, "LATCH_DIR", state_dir)
    # THE CLOCK, INTO `run_cohort` TOO. `main()` passes it no now_fn or
    # sleep_fn -- production reads the wall clock, which is right -- so a
    # harness driving the real entry point has to substitute the module's
    # own time functions, or the loop paces against real time while the
    # provider paces against the fake one and nothing ever advances.
    class _Time(object):
        time = staticmethod(clock)
        sleep = staticmethod(clock.sleep)
        strftime = staticmethod(time_mod.strftime)
        gmtime = staticmethod(time_mod.gmtime)

    monkeypatch.setattr(R, "time", _Time)
    captured: Dict[str, Any] = {}

    def provider_factory(*, progress, session_auth, recorder):
        p = PV.PmusWindowProvider(
            progress=progress, session_auth=session_auth,
            feed=FakeFeed(clock=clock), recorder=recorder,
            selector_fn=fake_selector, now_fn=clock,
            sleep_fn=clock.sleep, window_starts=list(starts),
            # THE VENUE ADVANCES ON ITS OWN, and reading it is when we
            # observe that. Nothing in the production path mirrors our posts
            # or triggers fills, so the pump belongs here, on the read.
            order_state_reader=_pumping_reader(venue))
        captured["provider"] = p
        captured["progress"] = progress
        return p

    rc = R.main([], provider_factory=provider_factory,
                progress_factory=lambda d: PG.CohortProgress(d),
                auth_factory=lambda d: AU.SessionAuthorization(d),
                recorder_factory=lambda d: RC.Recorder(
                    os.path.join(d, "tape")),
                transport_factory=_venue_transport(
                    venue, submit_raises=submit_raises))
    captured["rc"] = rc
    return captured


@pytest.fixture()
def probe_sid():
    return "E1-PROBE-TEST-SESSION"


def test_main_places_exactly_ONE_first_leg_and_binds_its_window(
        monkeypatch, tmp_path, probe_sid):
    state = str(tmp_path / "e1_cohort")
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, fake_pin(BASE)["slug"])
    FakeTransport.posts = []
    FakeTransport.cancels = []
    cap = _run_main(monkeypatch, state, venue=venue, clock=clock,
                    sid=probe_sid, starts=[BASE, BASE + W, BASE + 2 * W])
    assert cap["rc"] == 0
    # exactly ONE POST, ever
    assert len(FakeTransport.posts) == 1, FakeTransport.posts
    assert str(FakeTransport.posts[0]["quantity"]) == "0.13"
    assert FakeTransport.posts[0]["post_only"] is True
    # ONE window, bound and durable
    assert cap["progress"].windows_used() == 1
    bound = BUD.read_binding(session_id=probe_sid)
    assert bound is not None and bound["window"] == 0
    assert bound["IMMUTABLE"] is True
    # and the latch shows ONE spent attempt in that window
    assert L.window_state(window=0,
                          session_id=probe_sid)["attempts_spent"] == 1


def test_main_RESTART_after_an_AMBIGUOUS_submission_places_nothing(
        monkeypatch, tmp_path, probe_sid):
    """PROCESS 1 loses the POST's answer. PROCESS 2 starts on the same
    volume and must not place a replacement, must not rebind, and must not
    open another window."""
    state = str(tmp_path / "e1_cohort")
    starts = [BASE, BASE + W, BASE + 2 * W]

    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, fake_pin(BASE)["slug"])
    FakeTransport.posts = []
    FakeTransport.cancels = []
    cap1 = _run_main(monkeypatch, state, venue=venue, clock=clock,
                     sid=probe_sid, starts=starts,
                     submit_raises=O.SubmissionAmbiguous(
                         "504", detail={"status": 504}))
    # nothing acknowledged, but the attempt IS spent and the window IS bound
    assert FakeTransport.posts == []
    assert L.window_state(window=0,
                          session_id=probe_sid)["attempts_spent"] == 1
    bound1 = BUD.read_binding(session_id=probe_sid)
    assert bound1["window"] == 0
    # the durable intent is on the tape for the next process to find
    tape = os.path.join(state, "tape", "events.jsonl")
    rows = open(tape, encoding="utf8").read()
    assert "AMBIGUOUS_SUBMISSION" in rows
    assert "FIRST_LEG_SUBMISSION_INTENT" in rows

    # ---- PROCESS 2: a different process, the same volume ---------------
    clock2 = Clock(BASE + 40)
    venue2 = LoopVenue(clock2, fake_pin(BASE)["slug"])
    FakeTransport.posts = []
    FakeTransport.cancels = []
    cap2 = _run_main(monkeypatch, state, venue=venue2, clock=clock2,
                     sid=probe_sid, starts=starts)
    assert FakeTransport.posts == [], "a restart placed a second first leg"
    # the budget was NOT reset: still one spent slot, nothing added
    assert L.window_state(window=0,
                          session_id=probe_sid)["attempts_spent"] == 1
    # the binding is unchanged -- no rebind, no new window identity
    assert BUD.read_binding(session_id=probe_sid) == bound1
    # and no second window was consumed from the append-only ledger
    assert cap2["progress"].windows_used() == 1
    # AND IT FAILED CLOSED FOR A NAMED REASON. Two independent guards can
    # stop the restart and either is correct: the startup reconciliation
    # refuses while the unacknowledged intent is unresolved, and the window
    # binding refuses to open a different window. Whichever fires first, the
    # outcome asserted above is the same -- nothing placed, nothing rebound,
    # no window consumed.
    guards = _why_it_stopped(state, cap2)
    assert guards, "the restart stopped for no recorded reason"


def _why_it_stopped(state_dir, cap):
    """The named fail-closed reasons a restart can stop for."""
    found = []
    tape = os.path.join(state_dir, "tape", "events.jsonl")
    if os.path.exists(tape):
        rows = open(tape, encoding="utf8").read()
        for ev in ("STARTUP_RECONCILIATION_REFUSED",
                   "ATTEMPT_BUDGET_EXHAUSTED",
                   "FIRST_LEG_REFUSED_ATTEMPT_BUDGET"):
            if ev in rows:
                found.append(ev)
    prov = cap.get("provider")
    for r in (getattr(prov, "refusals", None) or []):
        why = str(r.get("refused"))
        if why.startswith("PROBE_IS_BOUND_TO_WINDOW") \
                or why.startswith("PROBE_WINDOW_BUDGET_SPENT"):
            found.append(why)
    return sorted(set(found))


def test_main_RESTART_after_a_CONFIRMED_FILL_adds_no_leg_and_keeps_the_fill(
        monkeypatch, tmp_path, probe_sid):
    """PROCESS 1 posts and the venue FILLS it. PROCESS 2 must not place
    another first leg, must not unwind the inventory, and must not open
    another window to 'try again'."""
    state = str(tmp_path / "e1_cohort")
    starts = [BASE, BASE + W, BASE + 2 * W]

    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, fake_pin(BASE)["slug"])
    # The venue fills whatever first leg appears, on its own schedule.
    # The time is close to the start deliberately: the suite's `_fast_pacing`
    # fixture shrinks the loop pace to 1 ms, so the injected clock advances a
    # millisecond per iteration and a fill scheduled seconds away would never
    # be reached inside the iteration cap.
    venue.fill_plan = [{"at": BASE + 10.05, "qty": None, "repeat": True,
                        "match": lambda oid, o: o["outcomeSide"] == UP}]
    FakeTransport.posts = []
    FakeTransport.cancels = []
    _run_main(monkeypatch, state, venue=venue, clock=clock, sid=probe_sid,
              starts=starts)
    assert len(FakeTransport.posts) >= 1
    filled = {oid: o for oid, o in venue.orders.items()
              if D(o["cumQuantity"]) > 0}
    assert filled, venue.orders

    # ---- PROCESS 2, same volume, with the fill visible at the venue ----
    clock2 = Clock(BASE + 120)
    venue.clock = clock2
    venue.fill_plan = []
    before = len(FakeTransport.posts)
    cap2 = _run_main(monkeypatch, state, venue=venue, clock=clock2,
                     sid=probe_sid, starts=starts)
    assert len(FakeTransport.posts) == before, \
        "a restart after a fill placed another order"
    assert cap2["progress"].windows_used() == 1, "a second window was opened"
    assert BUD.read_binding(session_id=probe_sid)["window"] == 0
    assert _why_it_stopped(state, cap2), "stopped for no recorded reason"
    # THE INVENTORY IS UNTOUCHED. No unwind, at any price, ever.
    for oid, o in filled.items():
        assert D(venue.orders[oid]["cumQuantity"]) == D(o["cumQuantity"])


def test_a_REBIND_to_another_window_is_REFUSED(monkeypatch, tmp_path,
                                              probe_sid):
    """The property the binding exists for: a probe that could rebind would
    collect a fresh attempt allowance in every window it chose."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setenv("PMUS_E1_SESSION_ID", probe_sid)
    BUD.bind_window(window=3, slug="cpc-btc-updown-15m-2026-10-08-1730z")
    again = BUD.bind_window(window=3,
                            slug="cpc-btc-updown-15m-2026-10-08-1730z")
    assert again["already_bound"] is True
    with pytest.raises(BUD.ProbeBindingConflict):
        BUD.bind_window(window=4,
                        slug="cpc-btc-updown-15m-2026-10-08-1745z")
    with pytest.raises(BUD.ProbeBindingConflict):
        BUD.bind_window(window=3, slug="some-other-market")
    # and the bound window still governs the attempt count
    b = one_probe(windows_used_at_start=3)
    assert b.attempt_gate(window=4)["because"] == \
        "PROBE_IS_BOUND_TO_WINDOW_3_NOT_4"
    assert b.attempt_gate(window=3)["may_attempt"] is True


def test_a_corrupt_binding_FAILS_CLOSED(monkeypatch, tmp_path, probe_sid):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setenv("PMUS_E1_SESSION_ID", probe_sid)
    p = BUD.binding_path(session_id=probe_sid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf8").write("{not json")
    with pytest.raises(BUD.BudgetNotAuthorized):
        BUD.read_binding(session_id=probe_sid)


def test_the_live_sessions_forty_historical_legs_do_not_exhaust_the_probe(
        monkeypatch, tmp_path):
    """THE EXACT CASE THAT BLOCKED THE AUTHORIZATION.

    The live cohort session carries forty first legs across windows 0..2 and
    nine remaining windows. `MAX_ATTEMPTS=1` must authorize ONE new leg in
    the probe's own window -- not read 40_OF_1 and refuse.
    """
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    live_sid = "E1-COHORT-20261008T005816Z-051888d8"
    monkeypatch.setenv("PMUS_E1_SESSION_ID", live_sid)
    for w, n in ((0, 0), (1, 20), (2, 20)):
        for _ in range(n):
            L.consume_attempt(window=w, slug="s", side=UP, price="0.49",
                              qty="0.13")
    assert L.cohort_state(live_sid)["attempts_spent_total"] == 40

    b = BUD.ProbeBudget(max_attempts=1, max_windows=1,
                        windows_used_at_start=3, source="owner:one_probe")
    # before the probe binds, window 3 is clean
    g = b.attempt_gate(window=3)
    assert g["may_attempt"] is True, g
    assert g["usage"]["session_history_is_read_never_recounted"] is True
    # bind it, spend the one attempt, and it is spent for good
    BUD.bind_window(window=3, slug="cpc-btc-updown-15m-2026-10-08-1730z")
    L.consume_attempt(window=3, slug="x", side=UP, price="0.49", qty="0.13")
    g2 = b.attempt_gate(window=3)
    assert g2["may_attempt"] is False
    assert g2["because"] == "ATTEMPT_BUDGET_EXHAUSTED_1_OF_1"
    # the history is still there, untouched
    assert L.cohort_state(live_sid)["attempts_spent_total"] == 41

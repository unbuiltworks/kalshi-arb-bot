"""The ~93-second window-roll stall of issue #195, reproduced and fixed.

WHAT WAS MEASURED IN PRODUCTION, which is what these tests encode.

Railway service `l5-pmus-live`, deployment 1603da04, 2026-11-06. Every
window roll for hours:

    L5_LIVE_TICK_STAGES {"total_s":93.651,"slowest":"roll","slowest_s":93.653}
    L5_LIVE_TICK_STAGES {"total_s":93.244,"slowest":"roll","slowest_s":93.242}
    L5_LIVE_TICK_STAGES {"total_s":92.979,"slowest":"roll","slowest_s":92.978}

The blocking call was located by bisecting the roll's own emitted lines
against their platform timestamps. For the 12:00z roll:

    12:00:02.031  L5_MODEL_WINDOW          (end of model_close)
    12:00:02.200  L5_SHADOW_WINDOW_CLOSE   (end of shadow_close)
    12:00:02.388  L5_CAMPAIGN_ROLLING      (end of emit_window_accounting)
       ... 96.7 seconds with nothing emitted ...
    12:01:39.066  L5_COST_BASIS_AUDIT      {"activity_walk":{"pages":133}}
    12:01:39.067  L5_LIVE_GATES            (the last line `roll` writes)

133 paginated GETs through the process's one shared limiter at
`PMUS_RPS` = 1.5 requests a second is 133 / 1.5 = 88.7 s of sleeping, plus
round trips. The platform's DNS stream shows it directly: `api.polymarket.us`
resolved every 0.666 s -- one request per 1/1.5 s -- continuously across the
gap.

WHAT IT COST. The first completion decision of the 0300z and 0315z windows,
the two that settled -$0.97, landed at +98.5 s and +97.5 s after the window
opened. Not because the book was thin -- the first 0300z decision reported
`displayed_depth: 1312.39` against a 1-contract need -- but because nothing
in the tick after `roll` had run yet. In 0315z the worst-case improvement an
executable completion offered decayed from $0.35 at that first evaluation to
$0.04 by the end of the window.

AND IT RAN EVERY WINDOW. The audit's comment said it "reads the account
activity feed ONCE"; the apply beneath it said "Boot only, so it cannot run
twice in a session". `roll` fires every fifteen minutes, so both ran
ninety-six times a day.

Nothing here opens a socket, uses a credential, or submits anything.
"""
from __future__ import annotations

import ast
import inspect
import time
from decimal import Decimal as D

import pytest

from src import l5_cost_basis_audit as CBA
from src import l5_pmus_live_runner as RUN
from src import l5_roll_diagnostics as RD
from src import l5_two_sided_shadow as TS


#: the production figure, so the reproduction is the measured one
OBSERVED_PAGES = 133
#: PMUS_RPS, the shared limiter's default read rate
OBSERVED_RPS = 1.5
#: what `roll` actually took, from L5_LIVE_TICK_STAGES above
OBSERVED_ROLL_S = 93.0


# =====================================================================
# 1. THE REPRODUCTION. A feed paced exactly as the live limiter paces it.
# =====================================================================

class PacedFeed:
    """A 133-page activity feed that sleeps like the shared limiter.

    `sleep` is injected so the test can measure the SCHEDULE without paying
    for it in wall clock: the virtual clock advances by 1/rps per page,
    which is what the limiter does, and the assertions are about that number
    rather than about how fast this machine is.
    """

    def __init__(self, pages: int = OBSERVED_PAGES, rps: float = OBSERVED_RPS
                 ) -> None:
        self.pages = int(pages)
        self.per_page_s = 1.0 / float(rps)
        self.elapsed_s = 0.0
        self.reads = 0

    def _get(self, path: str):
        # the limiter's wait, charged to the virtual clock
        self.elapsed_s += self.per_page_s
        self.reads += 1
        n = 0
        if "cursor=" in path:
            n = int(path.split("cursor=")[1])
        rows = [{"type": "activity_type_trade",
                 "trade": {"id": "p%d" % n, "marketSlug": "m", "qty": "1"}}]
        if n >= self.pages - 1:
            return {"activities": rows, "eof": True}
        return {"activities": rows, "nextCursor": str(n + 1)}


def test_the_inline_walk_costs_the_measured_ninety_three_seconds():
    """The defect, stated as a number. One pass over the live feed through
    the live limiter is ~89 s, which with round trips is the 93 s observed."""
    feed = PacedFeed()
    walk = CBA.walk_activities(feed, max_pages=600)
    assert walk["pages"] == OBSERVED_PAGES
    assert walk["complete"] is True
    assert feed.elapsed_s == pytest.approx(OBSERVED_PAGES / OBSERVED_RPS,
                                           rel=1e-6)
    assert feed.elapsed_s > 88.0
    # and that is the whole of the observed roll, within round trips
    assert abs(feed.elapsed_s - OBSERVED_ROLL_S) < 6.0


# =====================================================================
# 2. THE WALK IS RESUMABLE, AND RESUMING CHANGES NOTHING BUT THE PACING.
# =====================================================================

def test_a_paced_walk_reads_the_same_pages_in_the_same_order():
    single = CBA.walk_activities(PacedFeed(), max_pages=600)
    feed = PacedFeed()
    pw = RD.PagedWalk(CBA.walk_activities, feed, pages_per_step=12,
                      max_pages=600)
    steps = 0
    while not pw.done:
        pw.step(time.monotonic() + 5.0)
        steps += 1
        assert steps < 100, "the walk must make progress every step"
    assert pw.walk["rows"] == single["rows"]
    assert pw.walk["pages"] == single["pages"] == OBSERVED_PAGES
    assert pw.walk["complete"] is True
    assert feed.reads == OBSERVED_PAGES          # not one page re-read
    assert steps == 12                           # ceil(133 / 12)


def test_no_single_step_holds_the_limiter_for_more_than_its_slice():
    """The property that keeps an order poll or a book read from queueing
    behind a diagnostic: a step costs pages_per_step/rps, not pages/rps."""
    feed = PacedFeed()
    pw = RD.PagedWalk(CBA.walk_activities, feed, pages_per_step=12,
                      max_pages=600)
    pw.step(time.monotonic() + 5.0)
    assert feed.elapsed_s == pytest.approx(12 / OBSERVED_RPS, rel=1e-6)
    assert feed.elapsed_s < 9.0


def test_a_feed_that_serves_no_cursor_stops_instead_of_re_reading_page_one():
    """A resume that cannot say where to resume from would re-read page one
    for ever. `no_cursor` is the feed's end, so the walk stops there."""
    reads = []

    class NoCursor:
        def _get(self, path):
            reads.append(path)
            return {"activities": [{"type": "activity_type_trade"}]}

    pw = RD.PagedWalk(CBA.walk_activities, NoCursor(), pages_per_step=2,
                      max_pages=10)
    pw.step(None)
    assert pw.done is True
    assert pw.walk["ended"] == "no_cursor"
    assert pw.walk["next_cursor"] is None
    assert len(reads) == 1


def test_a_truncated_walk_is_incomplete_and_refuses_every_basis():
    """`max_pages` is a truncation, and a basis computed from a truncated
    feed could be missing fills -- so none is computed from one."""

    class Store:
        state = {"leg_cost_basis_unknown": ["m|UP"],
                 "positions": {"m|UP": "1"}}

        def leg_cost(self, _m):
            return {"UP": D("1")}

    pw = RD.PagedWalk(CBA.walk_activities, PacedFeed(pages=50),
                      pages_per_step=4, max_pages=8)
    while not pw.done:
        pw.step(None)
    assert pw.walk["ended"] == "max_pages"
    assert pw.walk["complete"] is False
    out = CBA.audit(Store(), PacedFeed(pages=1), walk=pw.walk)
    reasons = [(l.get("basis") or {}).get("reason") for l in out["legs"]]
    assert any(str(r).startswith("ACTIVITY_WALK_INCOMPLETE") for r in reasons)


def test_the_single_shot_signature_still_works_unchanged():
    """Existing callers pass neither a cursor nor rows."""
    walk = CBA.walk_activities(PacedFeed(pages=3), max_pages=600)
    assert walk["pages"] == 3 and walk["complete"] is True
    assert set(walk) >= {"rows", "pages", "ended", "complete", "next_cursor"}


def test_the_audit_accepts_a_walk_it_did_not_make_and_reads_nothing_more():
    class Refuses:
        def _get(self, _path):
            raise AssertionError("the audit must not re-walk a supplied feed")

    class Store:
        state: dict = {}

    walk = CBA.walk_activities(PacedFeed(pages=2), max_pages=600)
    out = CBA.audit(Store(), Refuses(), walk=walk)
    assert out["ok"] is True and out["writes"] == "NONE"
    assert out["activity_walk"]["pages"] == 2


# =====================================================================
# 3. `roll` NO LONGER WALKS ANYTHING. THIS IS THE REGRESSION GUARD.
# =====================================================================

FORBIDDEN_IN_ROLL = (
    "CBA.audit", "walk_activities", "apply_cost_basis_corrections",
    "DC.from_tape", "TR.from_env", "establish_l5_baseline",
    "transport.account()", "transport.positions()",
)


def test_roll_calls_no_paginated_walk_and_no_optional_venue_read():
    src = inspect.getsource(RUN.Runner.roll)
    for name in FORBIDDEN_IN_ROLL:
        assert name not in src, "%s is back on the window-roll path" % name


def test_roll_still_does_every_thing_that_is_not_a_diagnostic():
    """DEFERRING IS NOT DELETING, and these are the calls that must not have
    moved: the window summary, the closes, the accounting, the transport and
    executor rebuild, the ownership oracle and the gates."""
    src = inspect.getsource(RUN.Runner.roll)
    for name in ("self.model_close", "self.close_window", "self.shadow_close",
                 "self.two_sided_close", "self.emit_window_accounting",
                 "T.PmusTransport(", "self.store.owns_venue_order_id",
                 "LX.FaithfulExecutor(", "L5_LIVE_GATES",
                 "self.shadow_attach_ladder", "self.evidence_attach"):
        assert name in src, "%s left the roll path" % name


def test_the_enqueue_is_the_only_diagnostic_statement_left_in_roll():
    src = inspect.getsource(RUN.Runner.roll)
    assert src.count("self.enqueue_roll_diagnostics()") == 1


def test_the_drain_runs_after_every_trading_stage_in_the_tick():
    """Position is the fix. A diagnostic may only use time the completion
    decision, the requote and the opening trigger have finished with."""
    src = inspect.getsource(RUN.Runner.tick)
    drain = src.index("self.drain_roll_diagnostics(now)")
    for earlier in ("self.maybe_ts_completion(now)",
                    "self.maybe_book_completion(now)",
                    "self.maybe_controller_open(now)",
                    "self.maybe_completion_requote(now)"):
        assert src.index(earlier) < drain, earlier
    # and it is still before settlement and reconciliation, which must not
    # be able to be skipped by a diagnostic that raises
    assert drain < src.index("self.maybe_preflight(now)")


def test_the_tick_reports_the_drain_as_its_own_stage():
    assert 'self.drain_roll_diagnostics(now)' in inspect.getsource(
        RUN.Runner.tick)
    assert '_sc.mark("roll_diagnostics")' in inspect.getsource(RUN.Runner.tick)


# =====================================================================
# 4. THE QUEUE'S OWN BEHAVIOUR.
# =====================================================================

def _queue():
    return RD.DeferredDiagnostics()


def test_nothing_runs_while_the_window_is_young():
    rd = _queue()
    ran = []
    rd.enqueue("j", lambda _d: ran.append(1) or {"ok": True})
    out = rd.drain(t_in_window_s=2.0, entry_critical=False)
    assert out["ran"] is None and out["reason"] == "WINDOW_TOO_YOUNG"
    assert ran == []
    assert rd.pending == ["j"]                   # deferred, not dropped


def test_nothing_runs_while_the_window_is_entry_critical():
    rd = _queue()
    ran = []
    rd.enqueue("j", lambda _d: ran.append(1) or {"ok": True})
    out = rd.drain(t_in_window_s=600.0, entry_critical=True)
    assert out["ran"] is None and out["reason"] == "ENTRY_CRITICAL"
    assert ran == [] and rd.pending == ["j"]


def test_an_unknown_window_age_defers_rather_than_runs():
    rd = _queue()
    rd.enqueue("j", lambda _d: {"ok": True})
    out = rd.drain(t_in_window_s=None, entry_critical=False)
    assert out["ran"] is None and out["reason"] == "WINDOW_AGE_UNKNOWN"
    assert rd.pending == ["j"]


def test_deferring_is_not_skipping_the_job_runs_once_the_window_is_quiet():
    rd = _queue()
    ran = []
    rd.enqueue("j", lambda _d: ran.append(1) or {"ok": True})
    for _ in range(20):
        rd.drain(t_in_window_s=5.0, entry_critical=False)
    assert ran == []
    out = rd.drain(t_in_window_s=600.0, entry_critical=False)
    assert out["ran"] == "j" and out["outcome"] == RD.DONE
    assert ran == [1]


def test_only_one_job_runs_per_drain():
    rd = _queue()
    order = []
    rd.enqueue("a", lambda _d: order.append("a") or {"ok": True})
    rd.enqueue("b", lambda _d: order.append("b") or {"ok": True})
    rd.drain(t_in_window_s=600.0)
    assert order == ["a"]
    rd.drain(t_in_window_s=600.0)
    assert order == ["a", "b"]


def test_a_resuming_job_keeps_its_place_at_the_head_of_the_queue():
    rd = _queue()
    seen = []

    def chunked(_d):
        seen.append(1)
        return {"_resume": len(seen) < 3, "step": len(seen)}

    rd.enqueue("chunked", chunked)
    rd.enqueue("after", lambda _d: {"ok": True})
    for _ in range(3):
        out = rd.drain(t_in_window_s=600.0)
        assert out["ran"] == "chunked"
    assert rd.pending == ["after"]
    assert len(seen) == 3


def test_a_raising_job_is_retired_not_retried_and_never_raises_out():
    rd = _queue()
    calls = []

    def boom(_d):
        calls.append(1)
        raise ValueError("feed shape changed")

    rd.enqueue("boom", boom)
    out = rd.drain(t_in_window_s=600.0)
    assert out["outcome"] == RD.FAILED
    assert "ValueError" in out["record"]["error"]
    assert rd.pending == []
    rd.drain(t_in_window_s=600.0)
    assert calls == [1], "a raising diagnostic must not be retried for ever"


def test_the_job_is_handed_a_deadline_from_the_budget():
    rd = _queue()
    got = []
    rd.enqueue("j", lambda d: got.append(d) or {"ok": True})
    before = time.monotonic()
    rd.drain(t_in_window_s=600.0, budget=4.0)
    assert got and before + 3.0 <= got[0] <= before + 5.5


def test_an_unreadable_budget_is_the_default_never_zero(monkeypatch):
    for bad in ("", "   ", "abc", "0", "-3"):
        monkeypatch.setenv(RD.BUDGET_S_ENV, bad)
        assert RD.budget_s() == RD.DEFAULT_BUDGET_S
        monkeypatch.setenv(RD.AFTER_S_ENV, bad)
        assert RD.after_s() == RD.DEFAULT_AFTER_S


def test_the_default_gate_is_past_the_windows_opening():
    """240 s into a 900 s window is past the entry cutoff and past the first
    completion evaluations, which is the point."""
    assert RD.DEFAULT_AFTER_S >= 180.0
    assert RD.DEFAULT_BUDGET_S <= 10.0
    assert RD.JOBS_PER_DRAIN == 1


def test_the_report_separates_pending_from_completed_and_failed():
    rd = _queue()
    rd.enqueue("ok", lambda _d: {"ok": True})
    rd.enqueue("bad", lambda _d: (_ for _ in ()).throw(RuntimeError("x")))
    rd.enqueue("waiting", lambda _d: {"ok": True})
    rd.drain(t_in_window_s=600.0)
    rd.drain(t_in_window_s=600.0)
    rep = rd.report()
    assert rep["completed"] == ["ok"]
    assert rep["failed"] == ["bad"]
    assert rep["pending"] == ["waiting"]


# =====================================================================
# 5. THE RUNNER'S OWN WIRING, bound onto a minimal object the way the
#    suite's other duck-typed runner tests do.
# =====================================================================

class _Store:
    def __init__(self, *, resting=0, up=D(0), dn=D(0), raises=False):
        self._resting, self._up, self._dn = resting, up, dn
        self._raises = raises
        self.state: dict = {}

    def still_fillable_order_count(self):
        if self._raises:
            raise RuntimeError("ledger unreadable")
        return self._resting

    def position(self, _market, side):
        return self._up if side == "UP" else self._dn


class _Transport:
    market_slug = "cpc-btc-updown-15m-2026-11-06-0300z"


class _Runner:
    """Only what `entry_critical` and `drain_roll_diagnostics` touch."""

    entry_critical = RUN.Runner.entry_critical
    drain_roll_diagnostics = RUN.Runner.drain_roll_diagnostics

    def __init__(self, **kw):
        import collections
        self.store = _Store(**kw)
        self.transport = _Transport()
        self.ws = 1000.0
        self.stats = collections.Counter()
        self.roll_diag = RD.DeferredDiagnostics()


def test_entry_critical_is_true_while_an_order_is_resting():
    assert _Runner(resting=1).entry_critical(1600.0) is True


def test_entry_critical_is_true_while_inventory_is_held():
    assert _Runner(up=D("1")).entry_critical(1600.0) is True
    assert _Runner(dn=D("2")).entry_critical(1600.0) is True


def test_entry_critical_is_true_on_an_unreadable_ledger():
    r = _Runner(raises=True)
    assert r.entry_critical(1600.0) is True
    assert any(k.startswith("entry_critical_unreadable") for k in r.stats)


def test_entry_critical_is_true_before_the_first_window():
    r = _Runner()
    r.ws = None
    assert r.entry_critical(1600.0) is True


def test_entry_critical_is_false_on_a_quiet_flat_market():
    assert _Runner().entry_critical(1600.0) is False


def test_the_drain_emits_the_jobs_record_under_the_jobs_own_kind(monkeypatch):
    seen = []
    monkeypatch.setattr(RUN, "emit", lambda k, d: seen.append((k, d)))
    r = _Runner()
    r.roll_diag.enqueue(
        "cost_basis_audit",
        lambda _d: {"_kind": "L5_COST_BASIS_AUDIT", "ok": True,
                    "writes": "NONE"})
    out = r.drain_roll_diagnostics(r.ws + 600.0)
    assert out["ran"] == "cost_basis_audit"
    assert [k for k, _ in seen] == ["L5_COST_BASIS_AUDIT"]
    doc = seen[0][1]
    assert doc["ok"] is True and doc["writes"] == "NONE"
    assert doc["deferred_job"]["outcome"] == RD.DONE
    assert "_kind" not in doc


def test_a_quiet_resume_step_is_reported_but_not_emitted(monkeypatch):
    seen = []
    monkeypatch.setattr(RUN, "emit", lambda k, d: seen.append((k, d)))
    r = _Runner()
    r.roll_diag.enqueue("j", lambda _d: {"_resume": True, "_emit": False,
                                         "_kind": "X"})
    out = r.drain_roll_diagnostics(r.ws + 600.0)
    assert out["outcome"] == RD.RESUMED
    assert seen == []


def test_a_failing_job_emits_a_named_failure_and_does_not_raise(monkeypatch):
    seen = []
    monkeypatch.setattr(RUN, "emit", lambda k, d: seen.append((k, d)))
    r = _Runner()
    r.roll_diag.enqueue(
        "cost_basis_audit",
        lambda _d: (_ for _ in ()).throw(KeyError("activities")))
    r.drain_roll_diagnostics(r.ws + 600.0)
    assert [k for k, _ in seen] == ["L5_ROLL_DIAGNOSTIC_FAILED"]


def test_the_drain_is_a_no_op_with_nothing_queued():
    r = _Runner()
    assert r.drain_roll_diagnostics(r.ws + 600.0) is None


def test_a_deferral_is_counted_so_a_stuck_queue_is_visible():
    r = _Runner(resting=1)
    r.roll_diag.enqueue("j", lambda _d: {"ok": True})
    r.drain_roll_diagnostics(r.ws + 600.0)
    assert r.stats["roll_diag:deferred:ENTRY_CRITICAL"] == 1


# =====================================================================
# 6. THE BUDGET LINE, so the next regression is one line and not a
#    forensic reconstruction.
# =====================================================================

def test_the_roll_reports_itself_over_budget():
    src = inspect.getsource(RUN.Runner.roll)
    assert "L5_LIVE_ROLL_OVER_BUDGET" in src
    assert "RD.roll_budget_s()" in src
    assert "_roll_t0 = time.monotonic()" in src


def test_the_budget_is_reported_and_never_enforced():
    """Nothing in `roll` is interruptible: the closes and the accounting must
    not be abandoned half-done, so the guard reports and does not cut off."""
    tree = ast.parse(inspect.getsource(RUN.Runner.roll).lstrip())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Return, ast.Raise)):
            seg = ast.unparse(node)
            assert "budget" not in seg.lower(), seg


def test_the_default_roll_budget_is_well_under_the_observed_stall():
    assert RD.DEFAULT_ROLL_BUDGET_S <= 15.0
    assert RD.DEFAULT_ROLL_BUDGET_S < OBSERVED_ROLL_S


# =====================================================================
# 7. ROLLOVER LATENCY, BEHAVIOURALLY: ENABLING THE DIAGNOSTICS MUST NOT
#    MAKE `roll` DO ANY WORK.
#
# The source scan above proves `roll` names none of the slow calls. This
# proves the stronger thing: with ALL FOUR switches on, the enqueue that
# replaced them touches no transport, no store and no tape, so a window
# roll costs the same whether they are enabled or not. That is the
# property the 93 seconds violated.
# =====================================================================

class _Exploding:
    """Anything touched here fails the test by raising."""

    def __getattr__(self, name):
        raise AssertionError(
            "the enqueue touched %r; enabling a diagnostic must not make "
            "`roll` do its work" % name)


class _EnqueueRunner:
    enqueue_roll_diagnostics = RUN.Runner.enqueue_roll_diagnostics

    def __init__(self):
        import collections
        self.roll_diag = RD.DeferredDiagnostics()
        self.stats = collections.Counter()
        # every collaborator a job would need, rigged to explode if used
        self.transport = _Exploding()
        self.store = _Exploding()
        self.tape = _Exploding()
        self.root = "/nonexistent"

    # the two jobs are bound methods on the real Runner; they must be
    # QUEUED, never called, so referencing them is all that may happen.
    _cost_basis_audit_step = RUN.Runner._cost_basis_audit_step
    _establish_baseline_step = RUN.Runner._establish_baseline_step


@pytest.fixture()
def _all_switches_on(monkeypatch):
    monkeypatch.setenv("L5_DEPTH_COMPARISON", "YES")
    monkeypatch.setenv("L5_COST_BASIS_AUDIT", "YES")
    monkeypatch.setenv("L5_ESTABLISH_BASELINE", "YES")
    monkeypatch.setenv("L5_TAPE_RECOVERY_COID", "l5f-something")


def test_enqueuing_every_diagnostic_touches_no_transport_store_or_tape(
        _all_switches_on):
    r = _EnqueueRunner()
    r.enqueue_roll_diagnostics()        # must not raise
    assert set(r.roll_diag.pending) == {
        "depth_comparison", "tape_recovery", "cost_basis_audit",
        "establish_baseline"}


def test_enqueuing_every_diagnostic_is_effectively_free(_all_switches_on):
    """Not a benchmark -- a bound. 93 s was the defect; this is the
    difference between queuing four closures and doing their work."""
    r = _EnqueueRunner()
    started = time.monotonic()
    r.enqueue_roll_diagnostics()
    assert time.monotonic() - started < 0.5


def test_a_second_roll_enqueues_nothing_further(_all_switches_on):
    """`roll` fires every fifteen minutes. The inline version did the work
    every time; the queue latches, so ninety-six rolls queue four jobs."""
    r = _EnqueueRunner()
    for _ in range(96):
        r.enqueue_roll_diagnostics()
    assert len(r.roll_diag.pending) == 4


def test_with_the_switches_off_nothing_is_queued_at_all(monkeypatch):
    for env in ("L5_DEPTH_COMPARISON", "L5_COST_BASIS_AUDIT",
                "L5_ESTABLISH_BASELINE", "L5_TAPE_RECOVERY_COID"):
        monkeypatch.delenv(env, raising=False)
    r = _EnqueueRunner()
    r.enqueue_roll_diagnostics()
    assert r.roll_diag.pending == []


def test_a_queued_job_only_reads_the_transport_when_it_actually_runs(
        _all_switches_on):
    """The complement of the test above: the work is not GONE, it is
    DEFERRED. Running the job must reach the transport -- otherwise the
    enqueue test would pass on a job that does nothing."""
    r = _EnqueueRunner()
    r.enqueue_roll_diagnostics()
    out = r.roll_diag.drain(t_in_window_s=600.0, entry_critical=False)
    # it ran, and it failed BECAUSE it reached the exploding collaborator
    assert out["ran"] is not None
    assert out["outcome"] == RD.FAILED


# =====================================================================
# 8. THE RESIDUAL COST AFTER THE 93 SECONDS WAS REMOVED.
#
# MEASURED ON THE LIVE DEPLOYMENT a342fccc, 2026-11-06, after the
# diagnostics moved off `roll`:
#
#   L5_LIVE_ROLL_STAGE  two_sided_close   6.158 s   <- new slowest stage
#   L5_LIVE_TICK_STAGES two_sided_settle  6.180 s   <- same cost, other stage
#   L5_LIVE_TICK_STAGES roll              6.400 s   (was 93.2 s)
#
# and the line sizes at those exact instants:
#
#   15:30:09.619  len 81920  truncated   <- two_sided_close's emit
#   15:30:17.568  len 29109  intact      <- two_sided_advance, 0.002 s
#   15:30:34.115  len 81920  truncated   <- two_sided_settle's emit
#
# A 29 KB write returns immediately; an 80 KB write blocks about six
# seconds and is TRUNCATED at the platform's 81,920-byte limit, so it was
# both slow and lossy. The whole report is already on the volume --
# `park` and `finalize` write it there -- so bounding the LOG copy loses
# nothing that was not already being discarded.
# =====================================================================

def _big_report(cells=40):
    return {
        "LABEL": "L5_TWO_SIDED_SHADOW",
        "market": "cpc-btc-updown-15m-2026-11-06-1530z",
        "ticks": 4000,
        "spread_histogram": {str(i): i for i in range(400)},
        "cells": [{"name": "cell-%d" % i, "depth_ticks": i % 5,
                   "matched_shares": "1", "residual_shares": "-1",
                   "residual_cost": "0.42",
                   "matched_pnl_modelled": "0.03",
                   "net_pnl_modelled": "-0.11",
                   "state": "PENDING",
                   "noise": "x" * 2000} for i in range(cells)],
        "discipline": "y" * 500,
    }


def test_a_small_report_is_returned_byte_identical():
    small = {"LABEL": "L5_TWO_SIDED_SHADOW", "market": "m", "cells": []}
    import json as _j
    assert _j.dumps(TS.bounded_for_log(small), default=str, sort_keys=True) \
        == _j.dumps(small, default=str, sort_keys=True)


def test_an_oversized_report_is_brought_inside_the_budget():
    import json as _j
    rep = _big_report()
    raw = len(_j.dumps(rep, default=str))
    assert raw > TS.LOG_BUDGET_BYTES, raw
    out = TS.bounded_for_log(rep)
    assert len(_j.dumps(out, default=str)) <= TS.LOG_BUDGET_BYTES


def test_the_bounded_report_stays_under_the_platform_line_limit():
    import json as _j
    out = TS.bounded_for_log(_big_report(cells=400))
    assert len(_j.dumps(out, default=str)) < TS.LOG_LINE_LIMIT


def test_the_pnl_and_inventory_figures_survive_the_reduction():
    out = TS.bounded_for_log(_big_report())
    assert out["cells_reduced"]["cells"] == 40
    for c in out["cells"]:
        for k in ("matched_shares", "residual_shares", "residual_cost",
                  "matched_pnl_modelled", "net_pnl_modelled", "state"):
            assert k in c, k
        assert c["_reduced"] is True
        assert "noise" not in c


def test_the_reduction_says_it_happened_and_why():
    out = TS.bounded_for_log(_big_report())
    cr = out["cells_reduced"]
    assert cr["full_bytes"] > cr["budget_bytes"]
    assert "shadow ledger on the volume" in cr["labels"]
    assert str(TS.LOG_LINE_LIMIT) in cr["labels"]


def test_the_market_and_label_are_never_dropped():
    out = TS.bounded_for_log(_big_report(cells=400))
    assert out["LABEL"] == "L5_TWO_SIDED_SHADOW"
    assert out["market"].endswith("1530z")


def test_an_unserialisable_report_is_returned_rather_than_raising():
    class Odd:
        pass
    rep = {"LABEL": "x", "cells": [], "weird": Odd()}
    out = TS.bounded_for_log(rep)        # must not raise
    assert out["LABEL"] == "x"


def test_the_ledger_still_stores_the_WHOLE_report(tmp_path):
    """The reduction is the LOG copy only. This is what makes it lossless."""
    led = TS.ShadowLedger(str(tmp_path / "s.json"))
    rep = _big_report()
    led.park(rep, now=1.0)
    stored = led.state["pending"][rep["market"]]
    assert len(stored["cells"]) == 40
    assert "noise" in stored["cells"][0]          # full detail retained
    assert "cells_reduced" not in stored


def test_every_runner_emit_of_the_report_is_bounded():
    """All four sites, so none can reintroduce the six seconds."""
    import inspect
    src = inspect.getsource(RUN)
    import re
    for m in re.finditer(r'emit\("L5_TWO_SIDED_SHADOW",\s*(.{0,40})',
                         src, re.S):
        assert "bounded_for_log" in m.group(1), m.group(1)
    assert src.count('emit("L5_TWO_SIDED_SHADOW"') == 4
    # FIVE SITES NOW. The fifth is L5_UNMATCHED_DISPOSITION, which is
    # bounded for the same reason: it is emitted on a tick, it embeds a
    # priced action list, and an unbounded line is what truncated the
    # heartbeat past the fields a live window is policed by.
    assert src.count("TS.bounded_for_log(") == 5


def test_a_very_large_grid_still_fits_because_cells_roll_up():
    """Stage 3. 400 digests are 80 KB, so reduction alone is not a bound."""
    import json as _j
    out = TS.bounded_for_log(_big_report(cells=400))
    assert len(_j.dumps(out, default=str)) <= TS.LOG_BUDGET_BYTES
    assert out["cells"]["_rollup"] is True
    assert out["cells"]["count"] == 400


def test_the_rollup_preserves_the_economic_totals():
    out = TS.bounded_for_log(_big_report(cells=400))
    tot = out["cells"]["totals"]
    # 400 cells x the fixed per-cell figures in _big_report
    assert D(tot["matched_shares"]) == D("400")
    assert D(tot["residual_shares"]) == D("-400")
    assert D(tot["residual_cost"]) == D("0.42") * 400
    assert D(tot["net_pnl_modelled"]) == D("-0.11") * 400
    assert out["cells"]["by_state"] == {"PENDING": 400}


def test_a_pathological_report_falls_back_to_a_bounded_floor():
    """Stage 4. A single enormous top-level field cannot defeat the bound."""
    import json as _j
    rep = _big_report(cells=400)
    rep["some_new_field"] = "z" * 200000
    out = TS.bounded_for_log(rep)
    assert len(_j.dumps(out, default=str)) <= TS.LOG_BUDGET_BYTES
    assert out["_log_floor"] is True
    assert out["LABEL"] == "L5_TWO_SIDED_SHADOW"
    assert out["market"].endswith("1530z")
    assert "some_new_field" not in out


def test_the_bound_holds_for_every_grid_size_we_could_ever_emit():
    import json as _j
    for n in (0, 1, 5, 40, 120, 400, 2000):
        out = TS.bounded_for_log(_big_report(cells=n))
        assert len(_j.dumps(out, default=str)) <= TS.LOG_BUDGET_BYTES, n
        assert len(_j.dumps(out, default=str)) < TS.LOG_LINE_LIMIT, n


def test_a_clamped_scalar_says_it_was_clamped():
    rep = _big_report(cells=400)
    rep["some_new_field"] = "z" * 200000
    rep["state"] = "s" * 5000
    out = TS.bounded_for_log(rep)
    assert out["state"].endswith("...[clamped]")
    assert len(out["state"]) < 300


# =====================================================================
# 9. THE BOUND IS SCOPED TO THE OBSERVABILITY LINE AND NOTHING ELSE.
#
# The reduction must not reach the records that are authoritative:
# settlement, reconciliation, the window accounting, or the durable
# shadow ledger on the volume. Those are read to decide what is REALIZED,
# and a reduced one would be a silently incomplete financial record.
# =====================================================================

AUTHORITATIVE = (
    "L5_LIVE_SETTLED", "L5_LIVE_RECONCILE", "L5_LIVE_WINDOW",
    "L5_CAMPAIGN_ROLLING", "L5_LIVE_GATES", "L5_TS_COMPLETION",
    "L5_TS_COMPLETION_RESULT", "L5_SHADOW_COMPLETION_SETTLED",
)


@pytest.mark.parametrize("label", AUTHORITATIVE)
def test_no_authoritative_record_is_passed_through_the_bound(label):
    """Only L5_TWO_SIDED_SHADOW is reduced. Everything that decides what
    is realized is emitted whole.

    THE LABEL MUST EXIST FIRST. A parametrised test that walks regex
    matches passes VACUOUSLY when there are none, so a renamed or removed
    emit would read as a pass forever. The count is asserted before the
    content is.
    """
    import re
    src = inspect.getsource(RUN)
    sites = list(re.finditer(r'emit\("%s",\s*(.{0,60})' % re.escape(label),
                             src, re.S))
    assert sites, "no emit site for %s -- this test was vacuous" % label
    for m in sites:
        assert "bounded_for_log" not in m.group(1), (label, m.group(1))


def test_the_bound_is_applied_to_exactly_the_declared_labels():
    """Two, and both are tick-rate records carrying a nested report.

    Kept as an allow-list rather than a count so a THIRD label cannot
    acquire the bound silently -- the point of the original single-label
    assertion. `L5_UNMATCHED_DISPOSITION` was added deliberately with the
    hold-versus-complete decision engine.
    """
    import re
    src = inspect.getsource(RUN)
    labels = set()
    for m in re.finditer(r'emit\("([A-Z0-9_]+)",\s*(?:\n\s*)?'
                         r'(?:[A-Za-z_.]*\()?TS\.bounded_for_log', src):
        labels.add(m.group(1))
    assert labels == {"L5_TWO_SIDED_SHADOW",
                      "L5_UNMATCHED_DISPOSITION"}, labels


def test_the_ledgers_own_writers_do_not_reduce():
    """park and finalize are the durable copy; neither may bound."""
    for fn in (TS.ShadowLedger.park, TS.ShadowLedger.finalize):
        assert "bounded_for_log" not in inspect.getsource(fn)


def test_bounded_for_log_does_not_mutate_what_it_is_given():
    """The caller still holds the full report to store on the volume."""
    rep = _big_report(cells=400)
    before_cells = len(rep["cells"])
    before_hist = len(rep["spread_histogram"])
    TS.bounded_for_log(rep)
    assert len(rep["cells"]) == before_cells
    assert len(rep["spread_histogram"]) == before_hist
    assert "cells_reduced" not in rep
    assert "noise" in rep["cells"][0]


# =====================================================================
# 10. THE INSTRUMENTATION THAT CAME AFTER THE FIX DID NOT WORK.
#
# Bounding the log line was deployed on 7e3d1e1b at a31fe8fd4 and the
# reduction DID work -- the live line carries
# `cells_reduced {"cells": 27, "full_bytes": 155943, "budget_bytes":
# 48000}` and every cell is `_reduced` -- while the roll cost did not
# move at all:
#
#   before, 8 windows   6.400 6.606 7.437 6.649 6.683 6.417 7.984 6.558
#                       mean 6.882
#   after, 17:45z       6.511          two_sided_settle 6.315
#
# So the log write was NOT the cost. The hypothesis is falsified by its
# own fix. `report()` and `score_report()` were measured next and are
# free -- 0.030 s for all 189 ticks of the live window.
#
# The remaining candidate is this ledger: `finalized` is unbounded, a
# scored report is ~156 KB, and `save` rewrites the WHOLE file on every
# park and every finalize -- twice a window, on a network volume. Local
# disk at the real record size:
#
#      N | file MB | save() s | 2x/window
#     96 |    15.3 |    0.064 |   0.127
#    480 |    76.6 |    0.469 |   0.937
#    960 |   153.2 |    0.807 |   1.615
#   1440 |   229.8 |    1.833 |   3.665
#
# which reaches the observed range at a plausible N once the volume is
# slower than local disk. PLAUSIBLE IS NOT MEASURED, and the last
# plausible hypothesis was wrong, so nothing is changed on this belief
# until the running service reports its own number.
#
# IT REPORTED IT. Deployment 18deb02d, 2026-11-08T00:06:24Z:
#
#   L5_SHADOW_LEDGER {"finalized": 2294, "pending": 0,
#                     "bytes": 292732040, "last_save_s": 6.7605,
#                     "saves_this_process": 9, "max_pending": 16}
#
# 2,294 windows, 292.7 MB, 6.76 s a save, twice a window. The hypothesis
# was right and the instrumentation is what established it. Issue #209
# replaced the whole-file rewrite with an append-only log; the two tests
# below that pinned the v1 ON-DISK FORMAT were rewritten to pin the new
# one, and every other test in this section still holds unchanged.
# =====================================================================

def test_save_records_its_own_duration_and_size(tmp_path):
    led = TS.ShadowLedger(str(tmp_path / "s.json"))
    led.state["finalized"] = {"w%d" % i: {"market": "w%d" % i} for i in range(5)}
    led.save()
    assert led.last_save_s is not None and led.last_save_s >= 0
    assert led.last_save_bytes == (tmp_path / "s.json").stat().st_size
    assert led.saves == 1
    led.save()
    assert led.saves == 2


def test_the_size_report_names_the_append_only_log(tmp_path):
    """It used to name `finalized` as the unbounded half. It still is
    unbounded -- nothing was deleted -- but it is no longer REWRITTEN, and
    the line now says which file is the history and which is the manifest."""
    led = TS.ShadowLedger(str(tmp_path / "s.json"))
    led.state["finalized"] = {"w%d" % i: {"market": "w%d" % i}
                              for i in range(7)}
    led.state["pending"] = {"p1": {"market": "p1"}}
    led.save()
    r = led.size_report()
    assert r["finalized"] == 7
    assert r["pending"] == 1
    assert r["max_pending"] == led.max_pending
    assert r["bytes"] == (tmp_path / "s.json").stat().st_size
    assert r["log_bytes"] == (tmp_path / "s.json.finalized.jsonl").stat().st_size
    assert r["saves_this_process"] == 1
    assert r["format"] == TS.LEDGER_FORMAT == 2
    assert r["save_error"] is None
    assert "APPEND-ONLY" in r["labels"]


def test_the_size_report_works_before_any_save(tmp_path):
    """A fresh process must not raise on the first settle pass."""
    led = TS.ShadowLedger(str(tmp_path / "s.json"))
    r = led.size_report()
    assert r["bytes"] is None and r["last_save_s"] is None
    assert r["saves_this_process"] == 0


def test_the_manifest_no_longer_carries_the_history(tmp_path):
    """This test used to pin the v1 contract -- the file at `path` is a dump
    of the whole state -- which is exactly the 292.7 MB that took 6.76 s to
    write. The fix is that the history is NOT in this file any more. Every
    record is still kept, one JSON line apiece, in the log beside it."""
    import json as _j
    led = TS.ShadowLedger(str(tmp_path / "s.json"))
    led.state["finalized"] = {"w1": {"market": "w1", "cells": [{"a": "1"}]}}
    led.state["pending"] = {"p1": {"market": "p1"}}
    led.save()
    doc = _j.loads((tmp_path / "s.json").read_text())
    assert doc["format"] == TS.LEDGER_FORMAT
    assert doc["pending"] == {"p1": {"market": "p1"}}
    assert "finalized" not in doc              # the history is not rewritten
    assert doc["finalized_count"] == 1
    log = (tmp_path / "s.json.finalized.jsonl").read_text().splitlines()
    assert len(log) == 1
    assert _j.loads(log[0]) == {
        "market": "w1", "record": {"market": "w1", "cells": [{"a": "1"}]}}


def test_the_runner_emits_the_ledger_size_once_a_settle_pass():
    src = inspect.getsource(RUN)
    assert src.count('emit("L5_SHADOW_LEDGER"') == 1
    assert "size_report()" in src


def test_a_failing_size_report_cannot_break_the_settle_pass():
    """It is instrumentation. It must not be able to stop settlement."""
    src = inspect.getsource(RUN.Runner.two_sided_settle)
    i = src.index('emit("L5_SHADOW_LEDGER"')
    assert "try:" in src[max(0, i - 400):i]
    assert "shadow_ledger_size_report_failed" in src


# =====================================================================
# 11. THE HEARTBEAT WAS TRUNCATED TOO, AND IT TOOK THE SAFETY FIELDS.
#
# Found during preflight for the authorized two-window live test, on
# deployment 84909719. The `L5_LIVE_HB` line arrives at exactly 81,921
# bytes -- the platform limit -- and the fields past the cut are the
# ones a live window has to be policed by:
#
#   readable      armed, halt_local_env, halt_latch, shadow_lock,
#                 owner_arm_token, preflight, approval_state,
#                 windows_remaining, submission_gate, http_429,
#                 seeded, submissions_spent
#   NOT PRESENT   realized_today, orders_uncertain,
#                 defensive_spent_usd, peak_usd, cash_deployed_usd,
#                 open_orders
#
# Field sizes at that moment:
#
#   shadow_controller   61,450   <- 75% of the line
#   retired_markets      4,550
#   stats_age_s          4,072
#   stats                3,945
#   campaign               968
#   submission_gate        748
#   day_realized           385
#
# The line was spent on shadow-lane observability while the safety state
# fell off the end. You cannot promise to disarm on a safety failure you
# cannot see, so this is a preflight blocker and not a cosmetic one.
# =====================================================================

def _hb(extra_bytes=0, **kw):
    hb = {"utc": "2026-11-06T18:05:00Z", "role": "live", "market": "m",
          "armed": False, "preflight": "PASSED", "owner_arm_token": True,
          "halt_local_env": False, "halt_latch": "CLEAR",
          "shadow_lock": False, "approval_state": "approval_stale",
          "windows_remaining": None,
          "submission_gate": {"blocked": True, "why": "NOT_ARMED"},
          "seeded": True, "submissions_spent": 166,
          "realized_today": "-14.74950000", "orders_uncertain": [],
          "defensive_spent_usd": "0", "peak_usd": "3.32",
          "cash_deployed_usd": "0", "open_orders": 0,
          "campaign": {"journal": 69}, "unresolved_accounting": {},
          "stats": {"a": 1}}
    if extra_bytes:
        hb["shadow_controller"] = {"blob": "x" * extra_bytes}
    hb.update(kw)
    return hb


SAFETY = ("armed", "halt_local_env", "halt_latch", "shadow_lock",
          "approval_state", "windows_remaining", "submission_gate",
          "realized_today", "orders_uncertain", "defensive_spent_usd",
          "peak_usd", "cash_deployed_usd", "open_orders", "campaign",
          "submissions_spent", "seeded", "owner_arm_token", "preflight")


def test_a_small_heartbeat_is_unchanged():
    import json as _j
    hb = _hb()
    assert _j.dumps(TS.bounded_hb(hb), sort_keys=True, default=str) \
        == _j.dumps(hb, sort_keys=True, default=str)


def test_an_oversized_heartbeat_is_brought_inside_budget():
    import json as _j
    out = TS.bounded_hb(_hb(extra_bytes=200000))
    assert len(_j.dumps(out, default=str)) <= TS.LOG_BUDGET_BYTES


def test_it_stays_under_the_platform_limit_that_truncated_it():
    import json as _j
    out = TS.bounded_hb(_hb(extra_bytes=400000))
    assert len(_j.dumps(out, default=str)) < TS.LOG_LINE_LIMIT


@pytest.mark.parametrize("field", SAFETY)
def test_every_safety_field_survives_the_reduction(field):
    """The whole point. These are what a live window is policed by."""
    out = TS.bounded_hb(_hb(extra_bytes=400000))
    assert field in out
    assert out[field] == _hb()[field]
    assert not (isinstance(out[field], dict)
                and out[field].get("_reduced"))


def test_the_bloated_field_is_the_one_reduced():
    out = TS.bounded_hb(_hb(extra_bytes=200000))
    assert out["shadow_controller"]["_reduced"] is True
    assert out["hb_reduced"]["fields"]["shadow_controller"] > 100000


def test_the_largest_reducible_field_goes_first():
    hb = _hb()
    hb["small_extra"] = {"b": "y" * 5000}
    hb["shadow_controller"] = {"b": "x" * 200000}
    out = TS.bounded_hb(hb)
    assert out["shadow_controller"].get("_reduced") is True
    # the small one was not needed, so it is untouched
    assert out["small_extra"] == {"b": "y" * 5000}


def test_protected_fields_are_never_reduced_even_if_they_are_the_bloat():
    """A huge submission_gate must still arrive whole: it is the gate."""
    import json as _j
    hb = _hb()
    hb["submission_gate"] = {"blocked": True, "why": "z" * 120000}
    out = TS.bounded_hb(hb)
    assert out["submission_gate"] == hb["submission_gate"]
    assert "_reduced" not in _j.dumps(out["submission_gate"])


def test_the_reduction_states_that_it_happened():
    out = TS.bounded_hb(_hb(extra_bytes=200000))
    r = out["hb_reduced"]
    assert r["budget_bytes"] == TS.LOG_BUDGET_BYTES
    assert "SAFETY" in r["labels"]
    assert "HB_NEVER_REDUCE" in r["labels"]


def test_bounded_hb_does_not_mutate_its_argument():
    hb = _hb(extra_bytes=200000)
    TS.bounded_hb(hb)
    assert "hb_reduced" not in hb
    assert hb["shadow_controller"]["blob"].startswith("x")


def test_the_runner_wraps_its_only_heartbeat_emit():
    src = inspect.getsource(RUN)
    assert src.count('emit("L5_LIVE_HB"') == 1
    assert 'emit("L5_LIVE_HB", TS.bounded_hb(' in src


def test_the_never_reduce_list_covers_what_the_owner_is_owed():
    """submissions, fills, inventory, fees, rebates, settled P&L."""
    for k in ("realized_today", "day_realized", "defensive_spent_usd",
              "open_orders", "live_orders", "positions", "inventory",
              "fees", "rebates", "orders_uncertain", "submission_gate"):
        assert k in TS.HB_NEVER_REDUCE, k


# =====================================================================
# 12. THE AUTHORIZATION PREFLIGHT VERDICT, IN THE HEARTBEAT.
#
# THE INCIDENT, 2026-11-06. The authorized 20:00z window of the
# two-window live test never armed:
#
#   L5_LIVE_DISARMED  market=...-2000z  why=AUTHORIZATION_EXPIRED
#                     not_after=2026-11-06T03:45:00+00:00
#                     boundary=l5_live_authorization.authorize_arming
#
# `L5_LIVE_AUTH_NOT_AFTER` had lapsed ~16 hours earlier. The preflight
# CAUGHT IT, at the 19:34 boot, 26 minutes before the window:
#
#   L5_AUTHORIZATION_PREFLIGHT  ok=false  fail=["absolute_deadline"]
#     {"check":"absolute_deadline","verdict":"FAIL","state":"EXPIRED",
#      "expires_in_s":-56974.4,"blocks":"AUTHORIZATION_EXPIRED (step 26)"}
#
# with every other check PASS, campaign_approval included. Nobody read
# that line, and the campaign looked ready in the heartbeat.
#
# WHY IT WAS NOT IN THE HEARTBEAT. The runner's own comment: "Its own
# line because the heartbeat already exceeds the log transport's
# per-line limit and its tail is truncated in production." The
# workaround for the truncation `bounded_hb` now fixes is the reason the
# verdict was absent from the line anyone was polling.
#
# So it goes back in, compact, and CONTINUOUS rather than boot-only --
# a deadline that lapses mid-campaign is now visible before the next
# window instead of after it.
# =====================================================================

def test_the_verdict_is_in_the_heartbeat_and_never_reduced():
    assert "authorization_preflight" in TS.HB_NEVER_REDUCE
    src = inspect.getsource(RUN)
    assert '"authorization_preflight": self.authorization_preflight_verdict()' \
        in src


def test_the_verdict_survives_an_oversized_heartbeat():
    """The whole point: it must not be the field that gets dropped."""
    hb = _hb(extra_bytes=400000)
    hb["authorization_preflight"] = {"ok": False,
                                     "fail": ["absolute_deadline"],
                                     "unknown": []}
    out = TS.bounded_hb(hb)
    assert out["authorization_preflight"]["ok"] is False
    assert out["authorization_preflight"]["fail"] == ["absolute_deadline"]


def test_an_unreadable_verdict_is_not_ok(monkeypatch):
    """A verdict we cannot compute must never read as a pass."""
    import src.l5_pmus_live_runner as R

    class Boom:
        @staticmethod
        def authorization_preflight(*a, **k):
            raise RuntimeError("env unreadable")

    class FakeRunner:
        root = "/tmp"
        transport = None
        stats = __import__("collections").Counter()
        authorization_preflight_verdict = \
            R.Runner.authorization_preflight_verdict

    monkeypatch.setattr(R, "AUTH", Boom)
    v = FakeRunner().authorization_preflight_verdict()
    assert v["ok"] is None          # NOT True, and not False either
    assert v["error"] == "RuntimeError"
    assert "never read this as ok" in v["labels"]


def test_the_verdict_is_compact_not_the_full_rows():
    """The full rows stay on their own line; the heartbeat carries the
    verdict. A heartbeat that re-embedded every row would reintroduce the
    size problem this whole fix exists to remove."""
    # ASSERT ON THE OUTPUT, NOT THE SOURCE TEXT. The old check grepped for
    # '"rows"' in the source, which cannot tell READING the rows from
    # EMBEDDING them -- and the verdict now reads them to report WHY each
    # check failed, while carrying only a small bounded map. The property
    # that matters is the size of what reaches the heartbeat, so that is
    # what is measured.
    import collections
    import json
    import tempfile

    class _Stub:
        authorization_preflight_verdict = \
            RUN.Runner.authorization_preflight_verdict

        def __init__(self):
            self.transport = type("T", (), {"market_slug": ""})()
            self.root = tempfile.mkdtemp()
            self.stats = collections.Counter()

    v = _Stub().authorization_preflight_verdict()
    assert "rows" not in v, sorted(v)
    for k in ("ok", "fail", "unknown"):
        assert k in v, k
    # the whole verdict stays small enough that it cannot be the field
    # `hb_reduced` has to drop
    assert len(json.dumps(v, default=str)) < 2048, len(json.dumps(v))
    # and the reason map is bounded in both dimensions
    src = inspect.getsource(RUN.Runner.authorization_preflight_verdict)
    assert "[:6]" in src and "[:80]" in src


def test_the_absolute_deadline_is_one_of_the_checks_it_reports():
    """The check that cost the 20:00z window must be among those whose
    failure the heartbeat would now surface."""
    from src import l5_live_authorization as A
    pf = A.authorization_preflight("")
    names = [r["check"] for r in pf["rows"]]
    assert "absolute_deadline" in names
    assert "campaign_approval" in names
    # and a FAIL anywhere makes ok false
    assert pf["ok"] == (not pf["fail"] and not pf["unknown"])

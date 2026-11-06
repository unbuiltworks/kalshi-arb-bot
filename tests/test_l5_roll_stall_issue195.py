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

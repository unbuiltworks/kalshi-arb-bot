"""Which call actually holds the tick, measured rather than inferred.

WHY THIS EXISTS. The ~110s that elapses before `maybe_preflight` was
attributed to `sweep_settlements` on the strength of a transitive source
scan -- which matched the NAME inside the docstrings of `two_sided_settle`
and `two_sided_close`, neither of which calls it. Its real call sites are
at lines 5362 and 5443, AFTER `maybe_preflight` at 5336 and 5438, so the
cap that was built and deployed could not have helped. It did not: 2245z
measured 109.8s pre-preflight against a 92.4-110.9s baseline, with
`settlement_sweep_read: 0` over 555s of uptime.

So this stops inferring. `_StageClock` records the elapsed time between
the calls `tick` already makes, and the record names the slowest first.

IT WRAPS NOTHING. `mark` is called BETWEEN the existing statements, so
every call expression in `tick` is byte-identical to before -- which is
load-bearing: several tests drive `tick` against a stub that records
method calls by name, and `test_l5_uncertainty_recovery` asserts their
ORDER. A wrapper would have broken both.

Nothing here opens a socket, uses a credential or submits anything.
"""
from __future__ import annotations

import re

from src import l5_pmus_live_runner as R


# ------------------------------------------------------------- the clock

def test_the_slowest_stage_is_named_first():
    c = R._StageClock()
    c.stages = [("roll", 0.4), ("two_sided_settle", 88.1), ("model_advance", 1.2)]
    rep = c.report()
    assert rep["slowest"] == "two_sided_settle"
    assert rep["slowest_s"] == 88.1
    assert list(rep["stages_s"]) == ["two_sided_settle", "model_advance", "roll"]


def test_every_stage_is_carried_not_just_the_slowest():
    c = R._StageClock()
    c.stages = [("a", 1.0), ("b", 2.0), ("c", 3.0)]
    assert set(c.report()["stages_s"]) == {"a", "b", "c"}


def test_an_empty_pass_reports_no_slowest_rather_than_raising():
    rep = R._StageClock().report()
    assert rep["slowest"] is None and rep["slowest_s"] is None
    assert rep["stages_s"] == {}


def test_marks_accumulate_in_call_order_and_total_is_their_sum():
    c = R._StageClock()
    for n in ("roll", "two_sided_settle", "venue_cooling"):
        c.mark(n)
    assert [n for n, _ in c.stages] == ["roll", "two_sided_settle",
                                        "venue_cooling"]
    assert abs(c.total_s() - sum(v for _, v in c.stages)) < 1e-6


def test_a_junk_stage_name_is_recorded_not_raised():
    c = R._StageClock()
    c.mark(None)
    c.mark(1234)
    assert [n for n, _ in c.stages] == ["None", "1234"]


# -------------------------------------------- it cannot change the tick

def test_the_threshold_keeps_a_normal_tick_silent():
    """The loop runs every ~3s. A per-tick line would bury the log, and
    the delay being hunted is ~110s."""
    assert R.TICK_STAGE_REPORT_S == 5.0
    c = R._StageClock()
    c.stages = [("roll", 0.2)]
    c.last = c.t0 + 0.2
    assert c.total_s() < R.TICK_STAGE_REPORT_S


def test_every_call_expression_in_tick_is_unchanged():
    """THE LOAD-BEARING PROPERTY. Stubs in test_l5_uncertainty_recovery and
    test_l5_mechanism_tick_wiring record `tick`'s calls BY NAME and assert
    their order, so the clock must sit between the statements and never
    around them."""
    import inspect
    src = inspect.getsource(R.Runner.tick)
    for call in ("self.roll(now)", "self.maybe_halt_deadman(now)",
                 "self.maybe_expire_authorization(now)",
                 "self.maybe_cancel_orphans(now)",
                 "self.maybe_qm_deadline(now)",
                 "self.maybe_acc_deadline(now)",
                 "self.maybe_ts_completion(now)",
                 "self.maybe_ts_deadline(now)",
                 "self.model_advance(now)",
                 "self.two_sided_advance(now)",
                 "self.two_sided_settle(now)",
                 "cooling = self.venue_cooling()",
                 "self.maybe_preflight(now)"):
        assert call in src, call
    # and no call is wrapped: a mark is always its own statement
    for line in src.splitlines():
        st = line.strip()
        if "_sc.mark(" in st:
            assert st.startswith("_sc.mark("), st


def test_the_clock_is_created_before_roll_and_read_before_preflight():
    import inspect
    src = inspect.getsource(R.Runner.tick)
    assert src.index("_sc = _StageClock()") < src.index("self.roll(now)")
    assert src.index("_sc.total_s()") < src.index("self.maybe_preflight(now)")


def test_the_emit_is_guarded_so_reporting_cannot_kill_a_tick():
    import inspect
    src = inspect.getsource(R.Runner.tick)
    block = src[src.index("L5_LIVE_TICK_STAGES"):]
    assert "except BaseException" in block
    assert "tick_stage_emit_error" in block
    # KeyboardInterrupt and SystemExit still propagate
    assert "(KeyboardInterrupt, SystemExit)" in block


def test_every_pre_preflight_call_has_a_mark():
    """All sixteen, so the holder of the tick cannot hide in an
    unmeasured gap. A new stage must be MARKED and declared, which is
    exactly what this guard is for -- `maybe_completion_requote` was the
    fourteenth, `roll_diagnostics` the fifteenth, and
    `maybe_unmatched_disposition` is the sixteenth. It runs immediately
    BEFORE the requote, so the disposition record describes the state the
    requote then acts on rather than the state it left behind.

    `roll_diagnostics` is the deferred read-only work that used to run
    inside `roll` and held it for 93 seconds on every window (issue #195).
    It is marked for precisely the reason this test exists: the cost moved,
    so the measurement has to move with it, or the next regression hides in
    an unmeasured gap exactly as the last one did.

    `maybe_portfolio_decision` is the eighteenth: the portfolio
    controller's read-only decision record, off unless the owner sets
    L5_PORTFOLIO_CONTROLLER=SHADOW. It is marked because it reads the
    ledger and the book on the tick, and a stage that does that must be
    visible in the timing even when it places nothing."""
    import inspect
    src = inspect.getsource(R.Runner.tick)
    head = src[:src.index("self.maybe_preflight(now)")]
    marked = set(re.findall(r'_sc\.mark\("([a-z_]+)"\)', head))
    assert marked == {
        "maybe_portfolio_decision",
        # the nineteenth: the steer's withdrawal of an opening quote. It
        # reads the ledger and the book and can call the deadman, so it is
        # marked like every other stage that can.
        "maybe_portfolio_withdraw",
        "roll", "maybe_halt_deadman", "maybe_expire_authorization",
        "maybe_cancel_orphans", "maybe_qm_deadline", "maybe_acc_deadline",
        "sample_residual_book", "maybe_ts_completion",
        "maybe_unmatched_disposition",
        "maybe_completion_requote", "maybe_ts_deadline",
        "model_advance", "two_sided_advance", "two_sided_settle",
        "venue_cooling", "roll_diagnostics",
        # the seventeenth: lifetime observations for resting orders, moved
        # off the shadow tick so no shadow guard can stand between a live
        # order and the record of the queue it sat in. Declared here because
        # that is what this guard is for.
        "calibration_live_tick"}, sorted(marked)

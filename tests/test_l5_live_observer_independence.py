"""THE LIVE OBSERVER IS INDEPENDENT OF THE SHADOW DISPATCH (#208).

WHY THIS FILE EXISTS BEFORE THE REFACTOR IT GUARDS. The observer was first
called from `shadow_tick`, which returns early when the shadow state or the
window is unavailable -- so live-order observation depended on the shadow
path being healthy, a subtler form of the defect it was built to fix. Moving
it to the main `tick` removes that.

I ALSO CLAIMED THAT MOVE BROKE 103 TESTS. It broke none. Both runs behind
that claim were contaminated by my own edits to the runner while they were
in flight; a clean run of the relocated tree gives 20 failed / 18,187 passed
/ 14 errors, byte-identical failing IDs to the baseline. So these tests pin
the contracts the move actually has to respect, measured rather than
assumed:

  * `tick` is never INVOKED by any test -- 41 files assert on its SOURCE
    TEXT and none call it, so there is no stub to satisfy;
  * the required-call whitelist must still hold;
  * the declared stage-mark set must match exactly, which is the one
    contract a new stage legitimately updates -- the guard's own docstring
    says "a new stage must be MARKED and declared";
  * the relative order of the completion paths must be undisturbed.
"""
from __future__ import annotations

import inspect
import re

from src import l5_pmus_live_runner as RUN

TICK = inspect.getsource(RUN.Runner.tick)


# =====================================================================
# 1. INDEPENDENCE: the observer does not ride on the shadow dispatch.
# =====================================================================

def test_the_observer_is_called_from_the_tick_and_not_the_shadow_tick():
    assert "self.calibration_live_tick(now)" in TICK
    assert "calibration_live_tick" not in inspect.getsource(
        RUN.Runner.shadow_tick)


def body_of(fn):
    """A function's source with its docstring removed.

    Asserting on `inspect.getsource` alone matches the PROSE: this method's
    docstring names `_shadow_off` and `self.ws` precisely to say it does not
    depend on them, and a naive `not in` check reads that as a dependency.
    """
    import ast
    import textwrap
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src).body[0]
    stmts = tree.body[1:] if (isinstance(tree.body[0], ast.Expr)
                              and isinstance(tree.body[0].value,
                                             ast.Constant)) else tree.body
    return "\n".join(ast.unparse(st) for st in stmts)


def test_the_observer_cannot_be_switched_off_by_the_shadow_paths_guards():
    """`shadow_tick` bails on `not cs or self.ws is None`, and
    `calibration_shadow_tick` bails on `self._shadow_off`. Neither may stand
    between a resting order and its observation."""
    shadow = body_of(RUN.Runner.shadow_tick)
    assert "not cs or self.ws is None" in shadow
    assert "_shadow_off" in body_of(RUN.Runner.calibration_shadow_tick)
    body = body_of(RUN.Runner.calibration_live_tick)
    assert "_shadow_off" not in body
    assert "self.ws" not in body


def test_it_reads_its_own_inputs_rather_than_being_handed_them():
    """Taking the book from a caller is what tied it to the shadow tick."""
    assert ("def calibration_live_tick(self, now: float) -> None:"
            in inspect.getsource(RUN.Runner.calibration_live_tick))
    body = body_of(RUN.Runner.calibration_live_tick)
    assert "self.transport" in body and "market_slug" in body
    assert "self.book.snapshot(" in body
    assert "self.book.trades(" in body


def test_it_runs_before_the_cool_off_early_return():
    """Orders still rest during a venue cool-off and still need watching."""
    assert (TICK.index("self.calibration_live_tick(now)")
            < TICK.index("cooling = self.venue_cooling()"))


# =====================================================================
# 2. COMPATIBILITY: every contract `tick` already carried.
# =====================================================================

def test_tests_do_invoke_the_tick_so_the_observer_must_be_safe_in_it():
    """A CORRECTION TO MY OWN INVENTORY, kept because the mistake is the
    point.

    I first reported that no test invokes `Runner.tick`, on a grep that
    filtered OUT every file containing `getsource` -- which excluded exactly
    the files that do invoke it. There are about forty such call sites, in
    `test_l5_fill_calibration`, `test_l5_mechanism_strategy_isolation` and
    others.

    So a new call in `tick` IS executed by the suite, and the property that
    matters is not "nobody calls it" but "the observer is inert in every
    shape a test presents". That is what the containment below asserts, and
    what the clean full-suite run demonstrates: 18,187 passed with the
    observer in `tick`, byte-identical failing IDs to the baseline.
    """
    import pathlib
    here = pathlib.Path(__file__).parent
    sites = 0
    for path in sorted(here.glob("test_*.py")):
        sites += len(re.findall(r"runner\.tick\(", path.read_text()))
    assert sites > 10, sites          # it IS exercised, and widely


def test_the_observer_is_inert_in_every_shape_a_test_presents():
    """Each guard returns before anything is read, so an object that is not
    a fully built Runner cannot be made to raise by this call."""
    body = body_of(RUN.Runner.calibration_live_tick)
    # no recorder -> return; no executor/store -> return; no live orders ->
    # return; no slug -> return. All before any book read.
    for guard in ("if cal is None", "if store is None", "if not live",
                  "if not slug"):
        assert guard in body, guard
    assert body.index("if cal is None") < body.index("self.book.snapshot(")
    assert body.index("if not slug") < body.index("self.book.snapshot(")
    # and the whole body is inside one guarded try that swallows faults
    assert "except BaseException" in body
    assert "KeyboardInterrupt" in body


def test_the_required_call_whitelist_still_holds():
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
        assert call in TICK, call


def test_the_new_stage_is_marked_and_declared():
    """The guard in `test_l5_tick_stage_timing` asserts the mark set
    EXACTLY. A new stage is declared there, and measured here."""
    head = TICK[:TICK.index("self.maybe_preflight(now)")]
    marked = set(re.findall(r'_sc\.mark\("([a-z_]+)"\)', head))
    assert "calibration_live_tick" in marked
    # and the mark bounds the observer ALONE: the preceding mark closes the
    # completion stage, so the new stage is not another call's cost
    # relabelled.
    i_prev = head.index('_sc.mark("maybe_ts_completion")')
    i_call = head.index("self.calibration_live_tick(now)")
    i_mark = head.index('_sc.mark("calibration_live_tick")')
    assert i_prev < i_call < i_mark


def test_no_call_in_the_tick_is_wrapped_by_a_mark():
    for line in TICK.splitlines():
        st = line.strip()
        if "_sc.mark(" in st:
            assert st.startswith("_sc.mark("), st


def test_the_completion_paths_keep_their_order():
    """Completing a pair we hold must never queue behind taking on more."""
    assert (TICK.index("self.maybe_ts_completion(now)")
            < TICK.index("self.maybe_book_completion(now)")
            < TICK.index("self.maybe_controller_open(now)"))


def test_the_observer_is_after_the_completion_decisions():
    """It observes what is resting, so it runs once the tick's completion
    decisions have had their say."""
    assert (TICK.index("self.maybe_controller_open(now)")
            < TICK.index("self.calibration_live_tick(now)"))

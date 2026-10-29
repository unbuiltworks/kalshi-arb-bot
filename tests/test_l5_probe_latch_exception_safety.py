"""The two-sided leg markers must not outlive the call that set them.

WHAT THEY ARE. `_probe_leg_pending` / `_probe_round_pending` are per-call
scratch set by the two-sided adoption inside `FaithfulExecutor.handle` and
consumed immediately before the wire. The strategy-ownership gate reads
`_probe_leg_pending`, and `fresh_book_requote` reads it OUTSIDE that method.

THE 1445z DEFECT. Nineteen refusal exits sit between the set and the
consume and none reset the markers, so a refused two-sided plan latched
them for the life of the process: 15 of 27 decisions died in the gate whose
whole purpose is to exclude the OTHER engine, self-sustaining because only
a successful submission cleared it. Fixed by clearing on ENTRY, which closes
every refusal path.

WHAT ENTRY-CLEARING CANNOT FIX. An EXCEPTION between the set and the consume
leaves the markers set until the next call, and `fresh_book_requote` reads
them in that gap. Entry-clearing cannot close it: the gap precedes the entry
it would clear at. Route B re-enters far more often than one L5 action at a
time, so the gap stops being rare.

WHY THESE ARE SOURCE ASSERTIONS. Reaching `ex.handle` through
`Runner.handle` needs the transport, the book, the intent gate and the
window all satisfied, and a fixture that heavy would be asserting its own
scaffolding. `test_l5_probe_leg_marker_leak.py` already pins this same
invariant from the source, and this file follows it.

TWO PLACEMENTS WERE TRIED AND REVERTED, which is why the structure is
asserted rather than just the behaviour. Splitting the executor's `handle`
into a wrapper over a renamed `_handle` broke ELEVEN review guards -- the
refusal inventory reported ZERO sites against an inventory of 44. Splitting
`Runner.handle` the same way broke THREE more that look for
`GATE.L5_FOLLOWED` and the emitted two-sided stages in that method's own
text. The guarantee belongs at the CALL SITE, where it costs no guard.
"""
from __future__ import annotations

import inspect

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R

BOTH = ("self._probe_leg_pending = None", "self._probe_round_pending = None")


def _runner_handle_src() -> str:
    return inspect.getsource(R.Runner.handle)


# ------------------------------------------------- the guarantee's shape
def test_the_executor_call_is_wrapped_in_try_finally():
    src = _runner_handle_src()
    i = src.index("rec = ex.handle(row, {}")
    head = src[:i]
    assert head.rstrip().endswith("try:"), head[-200:]


def test_the_finally_clears_both_markers():
    src = _runner_handle_src()
    i = src.index("rec = ex.handle(row, {}")
    tail = src[i:]
    j = tail.index("finally:")
    block = tail[j:j + 300]
    assert "ex._probe_leg_pending = None" in block
    assert "ex._probe_round_pending = None" in block


def test_it_is_finally_and_not_except():
    """`except` would swallow the exception and change every caller's error
    handling. `finally` re-raises unchanged and only clears the scratch."""
    src = _runner_handle_src()
    i = src.index("rec = ex.handle(row, {}")
    tail = src[i:i + 600]
    assert "finally:" in tail
    assert "except" not in tail


def test_nothing_was_renamed_so_the_review_guards_still_read_the_body():
    """The whole reason for this placement. These are the exact strings the
    three guards my second attempt broke were looking for."""
    src = _runner_handle_src()
    assert "GATE.L5_FOLLOWED" in src
    assert "rec = ex.handle(row, {}" in src
    assert not hasattr(R.Runner, "_handle_action")
    assert not hasattr(LX.FaithfulExecutor, "_handle")


def test_the_executors_entry_clear_is_still_present():
    """A first line of defence, not replaced: it is what closes the
    nineteen refusal exits from inside the executor."""
    src = inspect.getsource(LX.FaithfulExecutor.handle)
    head = src[:src.index("# INDEPENDENT HALT")]
    for stmt in BOTH:
        assert stmt in head


def test_the_executor_still_consumes_the_markers_before_the_wire():
    """The set and the consume both stay inside the executor; the runner's
    `finally` is additive and must not have removed either."""
    src = inspect.getsource(LX.FaithfulExecutor.handle)
    assert "self._probe_leg_pending = nxt" in src
    i_set = src.index("self._probe_leg_pending = nxt")
    i_consume = src.index("self._probe_leg_pending = None", i_set)
    assert i_consume > i_set


# ------------------------------------------------- the semantics it relies on
def test_a_finally_clears_on_every_exit_including_a_raise():
    """The language guarantee this rests on, pinned so a refactor that
    replaced `finally` with something weaker is caught here too."""
    state = {}

    class _Boom(RuntimeError):
        pass

    def call(mode):
        try:
            state["leg"] = "UP"
            if mode == "raise":
                raise _Boom()
            if mode == "interrupt":
                raise KeyboardInterrupt()
            return {"ok": True}
        finally:
            state["leg"] = None

    assert call("ok") == {"ok": True}
    assert state["leg"] is None
    with pytest.raises(_Boom):
        call("raise")
    assert state["leg"] is None
    with pytest.raises(KeyboardInterrupt):
        call("interrupt")
    assert state["leg"] is None


def test_the_book_completion_module_is_in_the_deployed_image():
    """It is lazily imported by `maybe_book_completion`, so absent from the
    image it would raise ModuleNotFoundError on the first tick with the
    flag set -- a production crash, not a test failure."""
    import pathlib
    dockerfile = (pathlib.Path(__file__).resolve().parents[1]
                  / "deploy" / "l5_pmus_live" / "Dockerfile").read_text()
    assert "COPY src/l5_book_completion.py" in dockerfile

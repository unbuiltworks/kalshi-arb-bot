"""The two-sided leg markers must not outlive the call that set them.

WHAT THEY ARE. `_probe_leg_pending` / `_probe_round_pending` are per-call
scratch set by the two-sided adoption inside `FaithfulExecutor.handle` and
consumed immediately before the wire. The strategy-ownership gate reads
`_probe_leg_pending` to decide whether the order in hand belongs to the
two-sided engine, and `fresh_book_requote` reads it OUTSIDE that method.

THE 1445z DEFECT. Nineteen refusal exits sit between the set and the
consume and none reset the markers, so a refused two-sided plan latched
them for the life of the process: 15 of 27 decisions then died in the gate
whose whole purpose is to exclude the OTHER engine, and the latch was
self-sustaining because only a successful submission cleared it. That was
fixed by clearing on ENTRY, which closes every refusal path.

WHAT ENTRY-CLEARING CANNOT FIX. An EXCEPTION between the set and the
consume leaves the markers set until the next call, and `fresh_book_requote`
reads them in that gap. Route B re-enters far more often than one L5 action
at a time, so the gap stops being rare.

WHY THE GUARANTEE IS THE RUNNER'S. It was first placed in the executor, by
splitting `handle` into a thin wrapper over a renamed `_handle`. That
worked and cost too much: eleven existing review guards introspect
`FaithfulExecutor.handle`'s SOURCE -- gate ordering, the marker reset
position, the refusal inventory -- and every one began reading the wrapper
instead of the body, with one reporting zero refusal sites against an
inventory of forty-four. Those guards are safety assets, so the guarantee
moved to `Runner.handle`, the only production caller, where it costs none
of them.
"""
from __future__ import annotations

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R


class _Boom(RuntimeError):
    pass


class _Ex:
    """Stands in for the executor: it only has to carry the markers."""

    def __init__(self) -> None:
        self._probe_leg_pending = None
        self._probe_round_pending = None


def _runner(body):
    """A Runner whose `_handle_action` is `body`. `Runner.handle` is the
    thing under test, so the body is stubbed deliberately: these tests are
    about the guarantee the caller makes, not what the executor decides."""
    r = R.Runner.__new__(R.Runner)
    r.executor = _Ex()
    r._handle_action = body
    return r


def _mark(r, leg="UP", rnd=2):
    r.executor._probe_leg_pending = leg
    r.executor._probe_round_pending = rnd


def _clear(r):
    return (r.executor._probe_leg_pending is None
            and r.executor._probe_round_pending is None)


def test_the_markers_are_cleared_after_a_normal_return():
    r = _runner(lambda row, recv: None)
    r_ = r

    def body(row, recv):
        _mark(r_)

    r._handle_action = body
    r.handle({}, 0.0)                      # Runner.handle returns None
    assert _clear(r)


def test_the_markers_are_cleared_after_a_refusal_return():
    r = _runner(lambda row, recv: None)

    def body(row, recv):
        _mark(r, "DOWN", 1)

    r._handle_action = body
    r.handle({}, 0.0)
    assert _clear(r)


def test_the_markers_are_cleared_when_the_body_raises():
    """The hole entry-clearing cannot close."""
    r = _runner(lambda row, recv: None)

    def body(row, recv):
        _mark(r, "UP", 3)
        raise _Boom("after the set, before the consume")

    r._handle_action = body
    with pytest.raises(_Boom):
        r.handle({}, 0.0)
    assert _clear(r)


def test_the_exception_is_re_raised_unchanged():
    """`finally`, not `except`: no caller's error handling moves."""
    sentinel = _Boom("exact instance")

    def body(row, recv):
        raise sentinel

    r = _runner(body)
    with pytest.raises(_Boom) as got:
        r.handle({}, 0.0)
    assert got.value is sentinel


def test_a_keyboard_interrupt_also_clears():
    """BaseException, not just Exception: a shutdown mid-plan must not
    leave a marker behind."""
    r = _runner(lambda row, recv: None)

    def body(row, recv):
        _mark(r)
        raise KeyboardInterrupt()

    r._handle_action = body
    with pytest.raises(KeyboardInterrupt):
        r.handle({}, 0.0)
    assert _clear(r)


def test_repeated_evaluations_cannot_accumulate_a_latch():
    """The Route B case: many calls, some raising. After every one the
    markers are clear, so call N+1 can never inherit call N's leg."""
    seen = []
    r = _runner(lambda row, recv: None)

    def body(row, recv):
        _mark(r, "UP", len(seen))
        seen.append(1)
        if len(seen) % 3 == 0:
            raise _Boom("every third")

    r._handle_action = body
    for i in range(30):
        try:
            r.handle({}, float(i))
        except _Boom:
            pass
        assert _clear(r), i
    assert len(seen) == 30


def test_a_missing_executor_does_not_turn_a_refusal_into_a_crash():
    """The clear is defensive about its own target: an early-constructed
    Runner with no executor must not raise from the `finally`."""
    r = R.Runner.__new__(R.Runner)
    r.executor = None
    r._handle_action = lambda row, recv: None
    r.handle({}, 0.0)


def test_the_arguments_pass_through_unchanged():
    got = {}

    def body(row, recv):
        got.update(row=row, recv=recv)

    r = _runner(body)
    r.handle({"action_key": "k"}, 123.5)
    assert got == {"row": {"action_key": "k"}, "recv": 123.5}


def test_the_executors_entry_clear_is_still_present():
    """A second line, not a replacement: the entry clear is what closes the
    nineteen refusal exits from inside the executor."""
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor.handle)
    head = src[:src.index("# INDEPENDENT HALT")]
    assert "self._probe_leg_pending = None" in head
    assert "self._probe_round_pending = None" in head


def test_no_other_module_calls_the_runners_private_handler():
    """Nothing may reach `_handle_action` and skip the guarantee.

    Scoped by name and by `self`: an earlier version of this guard matched
    any `._handle(` and failed on `l5_pmus_realtime_observer`, a different
    class calling its own unrelated method.
    """
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "src"
    guilty = []
    for py in sorted(root.rglob("*.py")):
        if py.name == "l5_pmus_live_runner.py":
            continue
        try:
            tree = ast.parse(py.read_text(errors="replace"))
        except SyntaxError:                                # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_handle_action"):
                guilty.append("%s:%d" % (py.name, node.lineno))
    assert guilty == [], guilty

"""The two-sided leg markers must not outlive the call that set them.

WHAT THEY ARE. `_probe_leg_pending` / `_probe_round_pending` are per-call
scratch set by the two-sided adoption inside `handle` and consumed
immediately before the wire. The strategy-ownership gate reads
`_probe_leg_pending` to decide whether the order in hand belongs to the
two-sided engine, and `fresh_book_requote` reads it OUTSIDE `handle`.

THE 1445z DEFECT. Nineteen refusal exits sit between the set and the
consume and none reset the markers, so a refused two-sided plan latched
them for the life of the process: 15 of 27 decisions then died in the gate
whose whole purpose is to exclude the OTHER engine, and the latch is
self-sustaining because only a successful submission cleared it. That was
fixed by clearing on ENTRY.

WHAT ENTRY-CLEARING CANNOT FIX, and what these tests cover: an EXCEPTION
between the set and the consume leaves the markers set until the next call,
and `fresh_book_requote` reads them in that gap. Under book-driven
re-evaluation `handle` is re-entered far more often, so the gap stops being
rare.
"""
from __future__ import annotations

import pytest

from src import l5_live_execution as LX


class _Boom(RuntimeError):
    pass


def _ex():
    """An executor whose `_handle` is replaced. The WRAPPER is under test,
    so the body is stubbed deliberately: these tests are about the
    guarantee the wrapper makes, not about what the body decides."""
    ex = LX.FaithfulExecutor.__new__(LX.FaithfulExecutor)
    ex._probe_leg_pending = None
    ex._probe_round_pending = None
    return ex


def test_the_markers_are_cleared_after_a_normal_return():
    ex = _ex()

    def body(*a, **k):
        ex._probe_leg_pending = "UP"
        ex._probe_round_pending = 2
        return {"ok": True}

    ex._handle = body
    assert ex.handle({}, {}, market="m", window="w") == {"ok": True}
    assert ex._probe_leg_pending is None
    assert ex._probe_round_pending is None


def test_the_markers_are_cleared_after_a_refusal_return():
    ex = _ex()

    def body(*a, **k):
        ex._probe_leg_pending = "DOWN"
        ex._probe_round_pending = 1
        return {"refused_by_layer": "SAFETY_OVERLAY"}

    ex._handle = body
    ex.handle({}, {}, market="m", window="w")
    assert ex._probe_leg_pending is None


def test_the_markers_are_cleared_when_the_body_raises():
    """The hole entry-clearing cannot close."""
    ex = _ex()

    def body(*a, **k):
        ex._probe_leg_pending = "UP"
        ex._probe_round_pending = 3
        raise _Boom("after the set, before the consume")

    ex._handle = body
    with pytest.raises(_Boom):
        ex.handle({}, {}, market="m", window="w")
    assert ex._probe_leg_pending is None
    assert ex._probe_round_pending is None


def test_the_exception_is_re_raised_unchanged():
    """`finally`, not `except`: no caller's error handling changes."""
    ex = _ex()
    sentinel = _Boom("exact instance")

    def body(*a, **k):
        raise sentinel

    ex._handle = body
    with pytest.raises(_Boom) as got:
        ex.handle({}, {}, market="m", window="w")
    assert got.value is sentinel


def test_a_keyboard_interrupt_also_clears():
    """BaseException, not just Exception. A shutdown mid-plan must not
    leave a marker for the next process image to inherit through a store."""
    ex = _ex()

    def body(*a, **k):
        ex._probe_leg_pending = "UP"
        raise KeyboardInterrupt()

    ex._handle = body
    with pytest.raises(KeyboardInterrupt):
        ex.handle({}, {}, market="m", window="w")
    assert ex._probe_leg_pending is None


def test_repeated_evaluations_cannot_accumulate_a_latch():
    """The book-driven case: many calls, some raising, some refusing.
    After every one of them the markers are clear, so call N+1 can never
    inherit call N's leg."""
    ex = _ex()
    seen = []

    def body(*a, **k):
        ex._probe_leg_pending = "UP"
        ex._probe_round_pending = len(seen)
        seen.append(1)
        if len(seen) % 3 == 0:
            raise _Boom("every third")
        return {"n": len(seen)}

    ex._handle = body
    for i in range(30):
        try:
            ex.handle({}, {}, market="m", window="w")
        except _Boom:
            pass
        assert ex._probe_leg_pending is None, i
        assert ex._probe_round_pending is None, i
    assert len(seen) == 30


def test_the_wrapper_passes_every_argument_through():
    ex = _ex()
    got = {}

    def body(row, auth, *, market, window, open_markets=()):
        got.update(row=row, auth=auth, market=market, window=window,
                   open_markets=open_markets)
        return {}

    ex._handle = body
    ex.handle({"action_key": "k"}, {"a": 1}, market="m", window="w",
              open_markets=("x", "y"))
    assert got == {"row": {"action_key": "k"}, "auth": {"a": 1},
                   "market": "m", "window": "w", "open_markets": ("x", "y")}


def test_the_real_handle_still_clears_on_entry():
    """The entry clear stays: it is what closes the nineteen refusal exits
    from INSIDE, and the wrapper is a second line, not a replacement."""
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor._handle)
    head = src[:src.index("# INDEPENDENT HALT")]
    assert "self._probe_leg_pending = None" in head
    assert "self._probe_round_pending = None" in head


def test_no_module_calls_the_executors_private_handle():
    """Nothing may reach `_handle` on the EXECUTOR and skip the guarantee.

    Scoped to the executor rather than to the substring: an earlier version
    matched any `._handle(` and failed on
    `l5_pmus_realtime_observer.py`, a different class calling its own
    unrelated `self._handle`. A guard that fires on an unrelated name is a
    guard that gets weakened to shut it up.
    """
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "src"
    guilty = []
    for py in sorted(root.rglob("*.py")):
        if py.name == "l5_live_execution.py":
            continue
        try:
            tree = ast.parse(py.read_text(errors="replace"))
        except SyntaxError:                                # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_handle"):
                continue
            recv = node.func.value
            # `self._handle` inside some other class is that class's own
            # method and is none of this invariant's business.
            if isinstance(recv, ast.Name) and recv.id == "self":
                continue
            guilty.append("%s:%d" % (py.name, node.lineno))
    assert guilty == [], guilty

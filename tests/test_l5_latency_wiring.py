"""The latency accumulator is wired, and cannot matter.

Two properties, and the second is the one that keeps it safe to ship into
a disarmed production run:

  1. It is CALLED from the four points that answer the owner's six
     questions -- the poll, the first sight of a held position, the
     completion decision, and the observer heartbeat -- and reported on the
     heartbeat.
  2. It is INERT. A raising accumulator counts a stat and nothing else; it
     cannot refuse a submission, cannot stop a cancellation, and cannot
     propagate out of the tick.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from collections import Counter

import pytest

from src import l5_pmus_live_runner as RUN


def body_of(fn):
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src).body[0]
    stmts = (tree.body[1:] if (isinstance(tree.body[0], ast.Expr)
                               and isinstance(tree.body[0].value,
                                              ast.Constant))
             else tree.body)
    return "\n".join(ast.unparse(st) for st in stmts)


class Boom:
    """An accumulator where every method raises."""

    def __getattr__(self, _n):
        def _f(*a, **k):
            raise RuntimeError("accumulator exploded")
        return _f


class Fake:
    """The attributes the helpers touch, and nothing else."""
    LATENCY_OBSERVER_KEY = RUN.Runner.LATENCY_OBSERVER_KEY

    def __init__(self, acc=None, slug="slug-a"):
        self.completion_latency = acc
        self.stats = Counter()
        self.now_t = 1000.0
        self._latency_last_slug = None
        self.transport = type("T", (), {"market_slug": slug})()

    _latency_safe = RUN.Runner._latency_safe
    _latency_observer_note = RUN.Runner._latency_observer_note
    _latency_report = RUN.Runner._latency_report


# ======================================================== 1. it is wired
def test_the_poll_is_noted_where_the_poll_actually_happens():
    b = body_of(RUN.Runner.poll_orders)
    assert "self.last_order_poll[coid] = self.now_t" in b
    assert "note_poll" in b


def test_the_held_side_is_noted_where_it_is_first_determined():
    b = body_of(RUN.Runner.maybe_book_completion)
    assert "note_fill_detected" in b
    # BEFORE the decision, or the interval it measures is backwards.
    assert b.index("note_fill_detected") < b.index("note_decision")


def test_the_decision_carries_the_age_the_controller_decided_on():
    b = body_of(RUN.Runner.maybe_book_completion)
    assert "note_decision" in b
    assert "book_age_s=rec.get('stream_age_s')" in b.replace('"', "'")


def test_the_observer_heartbeat_is_above_every_other_guard():
    """It has to be taken on the ticks where there is nothing to observe,
    or "the observer continued" cannot be told from "there were no orders".
    """
    b = body_of(RUN.Runner.calibration_live_tick)
    assert "_latency_observer_note" in b
    assert b.index("_latency_observer_note") < b.index("live_orders")
    assert b.index("_latency_observer_note") < b.index("market_slug")


def test_the_report_is_on_the_heartbeat():
    src = inspect.getsource(RUN.Runner)
    assert '"completion_latency": self._latency_report()' in src


def test_the_runner_builds_an_accumulator_and_a_slug_memo():
    src = inspect.getsource(RUN.Runner.__init__)
    assert "self.completion_latency = None" in src
    assert "LAT.CompletionLatency()" in src
    assert "self._latency_last_slug = None" in src


# ===================================================== 2. it cannot matter
def test_a_raising_accumulator_is_counted_and_swallowed():
    f = Fake(acc=Boom())
    f._latency_safe("note_poll", "m", 1.0)          # must not raise
    assert f.stats["latency_error:note_poll:RuntimeError"] == 1


def test_a_raising_accumulator_cannot_break_the_observer_heartbeat():
    f = Fake(acc=Boom())
    f._latency_observer_note()
    assert sum(v for k, v in f.stats.items()
               if k.startswith("latency_error:")) >= 1


def test_a_raising_report_yields_None_rather_than_a_broken_heartbeat():
    f = Fake(acc=Boom())
    assert f._latency_report() is None
    assert f.stats["latency_error:report:RuntimeError"] == 1


def test_no_accumulator_at_all_is_a_silent_no_op():
    f = Fake(acc=None)
    f._latency_safe("note_poll", "m", 1.0)
    f._latency_observer_note()
    assert f._latency_report() is None
    assert not f.stats


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_an_interrupt_is_never_swallowed(exc):
    class Interrupt:
        def __getattr__(self, _n):
            def _f(*a, **k):
                raise exc()
            return _f
    f = Fake(acc=Interrupt())
    with pytest.raises(exc):
        f._latency_safe("note_poll", "m", 1.0)


def test_the_helpers_hold_no_transport_and_submit_nothing():
    for fn in (RUN.Runner._latency_safe, RUN.Runner._latency_observer_note,
               RUN.Runner._latency_report):
        b = body_of(fn)
        for banned in ("submit", "place", "order(", "cancel"):
            assert banned not in b, (fn.__name__, banned)


# =============================================== 3. rollover, end to end
def test_a_slug_change_records_a_rollover_and_observation_continues():
    from src import l5_completion_latency as CL
    acc = CL.CompletionLatency()
    f = Fake(acc=acc, slug="slug-a")
    f._latency_observer_note()
    f._latency_observer_note()
    f.transport.market_slug = "slug-b"              # the window rolled
    f.now_t = 1100.0
    f._latency_observer_note()
    r = acc.report(RUN.Runner.LATENCY_OBSERVER_KEY)
    assert r["rollovers"] == 1
    assert r["observations_before_roll"] == 2
    assert r["observations_after_roll"] == 1
    assert r["observer_continued_across_rollover"] is True


def test_the_same_slug_twice_is_not_a_rollover():
    from src import l5_completion_latency as CL
    acc = CL.CompletionLatency()
    f = Fake(acc=acc, slug="slug-a")
    for _ in range(3):
        f._latency_observer_note()
    assert acc.report(RUN.Runner.LATENCY_OBSERVER_KEY)["rollovers"] == 0


def test_an_absent_slug_is_not_read_as_a_rollover():
    """A transport that has not yet resolved a market must not be charged
    a window roll on its first tick."""
    from src import l5_completion_latency as CL
    acc = CL.CompletionLatency()
    f = Fake(acc=acc, slug=None)
    f._latency_observer_note()
    f.transport.market_slug = "slug-a"
    f._latency_observer_note()
    r = acc.report(RUN.Runner.LATENCY_OBSERVER_KEY)
    assert r["rollovers"] == 0 and r["observations_before_roll"] == 2


def test_the_report_separates_the_observer_from_the_markets():
    from src import l5_completion_latency as CL
    acc = CL.CompletionLatency()
    acc.note_observation(RUN.Runner.LATENCY_OBSERVER_KEY, 1.0)
    acc.note_decision("mkt", 1.0, action="NO", reason="X", book_age_s=0.3)
    f = Fake(acc=acc)
    rep = f._latency_report()
    assert rep["observer"]["market"] == RUN.Runner.LATENCY_OBSERVER_KEY
    assert [m["market"] for m in rep["markets"]] == ["mkt"]


# ============================== 4. it is safe on a half-built object
#
# THIS CLASS OF MISTAKE HAPPENED TWICE, so it is pinned rather than
# remembered. A bare `self._latency_observer_note()` broke seven
# synthetic-transport rigs; a bare `self._latency_safe(...)` in
# `maybe_book_completion` and `poll_orders` then broke ten end-to-end
# window tests. Both are AttributeErrors on a rig that is not a fully
# built Runner -- and in production, on a Runner whose __init__ failed
# midway. Reporting-only telemetry must never be what stops a
# cancellation path from running.

def test_no_latency_call_site_invokes_the_helper_by_name_on_self():
    """Every site goes through `_lat(self, ...)` or a getattr lookup."""
    for fn in (RUN.Runner.maybe_book_completion, RUN.Runner.poll_orders,
               RUN.Runner.calibration_live_tick):
        b = body_of(fn)
        assert "self._latency_safe(" not in b, fn.__name__
        assert "self._latency_observer_note(" not in b, fn.__name__


def test_the_module_helper_is_a_no_op_on_an_object_without_the_method():
    class Bare:
        pass
    RUN._lat(Bare(), "note_poll", "m", 1.0)        # must not raise
    RUN._lat(None, "note_poll", "m", 1.0)


def test_the_module_helper_forwards_args_and_kwargs_when_it_can():
    seen = []

    class Rec:
        def _latency_safe(self, meth, *a, **kw):
            seen.append((meth, a, kw))
    RUN._lat(Rec(), "note_decision", "m", 2.0, reason="X")
    assert seen == [("note_decision", ("m", 2.0), {"reason": "X"})]


def test_the_observer_heartbeat_is_reached_by_getattr_in_the_tick_body():
    b = body_of(RUN.Runner.calibration_live_tick)
    assert "getattr(self, '_latency_observer_note', None)" in b.replace(
        '"', "'")

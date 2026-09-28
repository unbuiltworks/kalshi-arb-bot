"""1545z: arming must be retried after a PASSED preflight, not attempted once.

STATUS 2026-09-28 -- OPEN REQUIREMENT, DELIBERATELY RED. `Runner.try_arm` was
removed by 3a: the continuous runner is no longer permitted to arm, because exactly
one reviewed path -- `scripts/l5_pmus_live_oneshot.py` -- may own the arming
transition. That does NOT retire the requirement below. The one-shot currently calls
`probe.arm()` exactly once and aborts on refusal, so the 1545z lesson is implemented
NOWHERE, and this file stays as the standing record of that gap until the retry is
ported into the one-shot under the owner's ten conditions (same unspent unexpired
authorization, same market, no order submitted, transient refusal only, all
gates re-read per attempt, bounded deadline, consume exactly once).

It is marked xfail rather than deleted or skipped: deleting it would be the "merely
weaken the test" move that was ruled out, and skipping it would make the gap
invisible. xfail keeps it counted and reported while letting the rest of the suite
collect -- the AttributeError was at IMPORT time, which aborted collection for every
other file.

The live failure this pins down. On 2026-09-27 the executor was ARMED, the
owner arm token matched, preflight PASSED, and the 1545z window still ran
start to finish with `armed: false` and zero orders submitted.

Cause: `try_arm()` was reachable ONLY from inside `maybe_preflight()`, which
returns immediately once `preflight_status == "PASSED"`. Arming therefore got
exactly one attempt per market window. That single attempt at 15:45:23 found a
terminal order not yet booked, `orders_uncertain()` was set, try_arm refused --
correctly -- and by 15:45:35 the uncertainty had cleared with nothing left in
the process to ask again.

A refusal that clears on its own must not cost a whole market window.
"""
from __future__ import annotations

import collections

import pytest

from src import l5_pmus_live_runner as R

pytestmark = pytest.mark.xfail(
    reason="3a removed Runner.try_arm; the arm-retry requirement is OPEN and must "
           "be ported into scripts/l5_pmus_live_oneshot.py",
    raises=AttributeError, strict=True)

#: resolved at TEST time, not import time, so collection is never aborted
_MISSING = object()


class _Transport:
    def __init__(self) -> None:
        self.armed = False
        self.market_slug = "cpc-btc-updown-15m-2026-09-27-1545z"
        self.tick_size = "0.01"
        self.arms = 0

    def arm(self):
        self.arms += 1
        self.armed = True
        return {"armed": True, "market": self.market_slug}


class _Store:
    def __init__(self, uncertain=None) -> None:
        self._uncertain = uncertain

    def orders_uncertain(self):
        return self._uncertain


class _Runner:
    """Duck-typed stand-in carrying only what these two methods touch.

    Deliberately not a real Runner: the point is to drive the real
    `maybe_preflight` and `try_arm` code objects, not to boot a venue client.
    """

    maybe_preflight = getattr(R.Runner, "maybe_preflight", _MISSING)
    try_arm = getattr(R.Runner, "try_arm", _MISSING)

    def _require_try_arm(self):
        if self.try_arm is _MISSING:
            raise AttributeError(
                "Runner.try_arm was removed by 3a; the arm-retry requirement is "
                "OPEN and belongs in the one-shot probe")

    def __init__(self, *, uncertain=None) -> None:
        self.transport = _Transport()
        self.store = _Store(uncertain)
        self.preflight_status = "PASSED"
        self.preflight_attempts = 1
        self.last_preflight_t = 0.0
        self.last_blockers = None
        self.reconciled = True
        self.now_t = 0.0
        self.win = collections.Counter()
        self.preflights = 0
        self.reconciles = 0

    def halted(self) -> bool:
        return False

    def reconcile_if_needed(self, now: float) -> None:
        self.reconciles += 1


def test_a_passed_preflight_still_retries_the_arm():
    _Runner(uncertain=None)._require_try_arm()
    """The defect itself: PASSED used to return before reaching try_arm."""
    r = _Runner()
    r.maybe_preflight(100.0)
    assert r.transport.armed is True
    assert r.transport.arms == 1


def test_an_uncertainty_that_clears_does_not_cost_the_window():
    _Runner(uncertain=None)._require_try_arm()
    """The exact 1545z sequence, and what should have happened."""
    r = _Runner(uncertain="one terminal order is not yet booked")
    r.maybe_preflight(100.0)
    assert r.transport.armed is False
    assert r.win["arm_refused_orders_uncertain"] == 1

    # ...and the venue answers twelve seconds later, as it did live.
    r.store._uncertain = None
    r.maybe_preflight(112.0)
    assert r.transport.armed is True, (
        "a transient uncertainty at the one arm attempt must not disarm the "
        "runner for the rest of the market window")


def test_a_refusal_is_logged_once_however_long_it_lasts():
    _Runner(uncertain=None)._require_try_arm()
    r = _Runner(uncertain="still unbooked")
    emitted = []
    orig, R.emit = R.emit, lambda k, p: emitted.append((k, p))
    try:
        for i in range(40):
            r.maybe_preflight(100.0 + i)
    finally:
        R.emit = orig
    assert r.transport.armed is False
    assert r.win["arm_refused_orders_uncertain"] == 40
    disarms = [e for e in emitted if e[0] == "L5_LIVE_DISARMED"]
    assert len(disarms) == 1, "retrying must not turn one refusal into a flood"


def test_an_unreconciled_book_is_retried_on_the_same_terms():
    _Runner(uncertain=None)._require_try_arm()
    r = _Runner()
    r.reconciled = False
    r.maybe_preflight(100.0)
    assert r.transport.armed is False
    assert r.win["arm_refused_unreconciled"] == 1
    r.reconciled = True
    r.maybe_preflight(101.0)
    assert r.transport.armed is True


def test_the_retry_never_re_runs_a_passed_preflight():
    _Runner(uncertain=None)._require_try_arm()
    """Preflight is a fact about the market. Only arming is retried."""
    r = _Runner()
    before = r.preflight_attempts
    for i in range(10):
        r.maybe_preflight(100.0 + 30 * i)
    assert r.preflight_attempts == before
    assert r.preflight_status == "PASSED"


def test_the_retry_costs_nothing_once_armed():
    _Runner(uncertain=None)._require_try_arm()
    """try_arm returns at the first line when already armed: no reconcile,
    no second arm, nothing that could touch the venue."""
    r = _Runner()
    r.maybe_preflight(100.0)
    reconciles = r.reconciles
    for i in range(50):
        r.maybe_preflight(101.0 + i)
    assert r.transport.arms == 1
    assert r.reconciles == reconciles


def test_a_halted_runner_is_never_armed_by_the_retry():
    _Runner(uncertain=None)._require_try_arm()
    r = _Runner()
    r.halted = lambda: True
    for i in range(10):
        r.maybe_preflight(100.0 + i)
    assert r.transport.armed is False
    assert r.transport.arms == 0


def test_a_failed_preflight_still_takes_the_preflight_path():
    _Runner(uncertain=None)._require_try_arm()
    """The guard is additive: NOT_RUN/FAILED behaviour is untouched."""
    r = _Runner()
    r.preflight_status = "NOT_RUN"
    r.transport.preflight = lambda expect_account=None: {
        "preflight": "FAILED", "market_slug": r.transport.market_slug}
    r.last_preflight_t = -1e9
    orig, R.emit = R.emit, lambda k, p: None
    try:
        r.maybe_preflight(100.0)
    finally:
        R.emit = orig
    assert r.preflight_attempts == 2
    assert r.preflight_status == "FAILED"
    assert r.transport.armed is False

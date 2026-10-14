"""1545z: arming must be retried after a PASSED preflight, not attempted once.

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
import pathlib
import tempfile

import pytest

from src import l5_live_authorization as AUTH
from src import l5_pmus_live_runner as R
from src import pmus_auth as Q1


@pytest.fixture(autouse=True)
def _arming_boundary_satisfied(monkeypatch, tmp_path):
    """These tests are about RETRY REACHABILITY, not about authorization.

    `try_arm` now goes through `l5_live_authorization.authorize_arming`, the
    one shared boundary, which refuses without owner authorization, a capital
    envelope, a writable ledger, and with the shadow lock on -- and the lock
    is ON by default. None of that is what this file pins down, so the
    fixture satisfies the boundary and leaves the retry property under test.

    Nothing here is weakened: the boundary's own refusals are covered in
    tests/test_l5_arming_boundary.py, including that the shadow lock refuses
    first and by name.
    """
    sid = "arm-retry-session"
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, sid)
    monkeypatch.setenv(Q1.ARM_ENV, sid)
    monkeypatch.setenv("L5_SHADOW_ONLY", "0")
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth.jsonl"))
    monkeypatch.delenv(AUTH.MARKET_ENV, raising=False)
    monkeypatch.delenv(AUTH.NOT_AFTER_ENV, raising=False)
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "test-auth-holder")
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "test-auth-holder")
    # a pytest tmp_path is on the container root, which the durability
    # precondition REFUSES by design. That precondition has its own tests in
    # tests/test_l5_arming_boundary.py, including that its default is strict.
    monkeypatch.setattr(AUTH, "durability_refusal", lambda p: None)


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


class _Limits:
    """The capital envelope the boundary records into the claim."""

    def as_dict(self):
        return {"max_usd_per_market": "5", "max_total_exposure_usd": "5",
                "daily_realized_loss_halt_usd": "5"}


class _Executor:
    limits = _Limits()
    #: these tests drive arm RETRY, not the halt gate. The boundary refuses
    #: HALT_GATE_ABSENT when no independent halt is configured, so the stub
    #: has to carry one or every test here would refuse for the wrong reason.
    halt_gate = object()


class _Runner:
    """Duck-typed stand-in carrying only what these two methods touch.

    Deliberately not a real Runner: the point is to drive the real
    `maybe_preflight` and `try_arm` code objects, not to boot a venue client.
    """

    maybe_preflight = R.Runner.maybe_preflight
    try_arm = R.Runner.try_arm

    def __init__(self, *, uncertain=None) -> None:
        self.transport = _Transport()
        self.store = _Store(uncertain)
        self.executor = _Executor()
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
    """The defect itself: PASSED used to return before reaching try_arm."""
    r = _Runner()
    r.maybe_preflight(100.0)
    assert r.transport.armed is True
    assert r.transport.arms == 1


def test_an_uncertainty_that_clears_does_not_cost_the_window():
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
    r = _Runner()
    r.reconciled = False
    r.maybe_preflight(100.0)
    assert r.transport.armed is False
    assert r.win["arm_refused_unreconciled"] == 1
    r.reconciled = True
    r.maybe_preflight(101.0)
    assert r.transport.armed is True


def test_the_retry_never_re_runs_a_passed_preflight():
    """Preflight is a fact about the market. Only arming is retried."""
    r = _Runner()
    before = r.preflight_attempts
    for i in range(10):
        r.maybe_preflight(100.0 + 30 * i)
    assert r.preflight_attempts == before
    assert r.preflight_status == "PASSED"


def test_the_retry_costs_nothing_once_armed():
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
    r = _Runner()
    r.halted = lambda: True
    for i in range(10):
        r.maybe_preflight(100.0 + i)
    assert r.transport.armed is False
    assert r.transport.arms == 0


def test_a_failed_preflight_still_takes_the_preflight_path():
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

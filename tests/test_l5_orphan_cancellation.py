"""An order we own but can no longer manage must still be withdrawn.

Every cancellation path in the runner was gated on `transport.armed`, so the
one state a crash actually leaves behind -- an order resting while the process
cannot arm -- was the one state nothing covered. These tests pin the recovery
path and its limits.
"""
from __future__ import annotations

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R


class _Transport(object):
    def __init__(self, armed=False):
        self.armed = armed
        self.market_slug = "cpc-btc-updown-15m-2026-10-14-1800z"
        self.disarmed = None

    def disarm(self, reason):
        self.armed = False
        self.disarmed = reason


class _Store(object):
    def __init__(self, n=1):
        self.n = n
        self.uncertain = None

    def live_order_count(self):
        return self.n

    def mark_uncertain(self, why):
        self.uncertain = why


class _Runner(object):
    ORPHAN_SWEEP_EVERY_S = R.Runner.ORPHAN_SWEEP_EVERY_S
    maybe_cancel_orphans = R.Runner.maybe_cancel_orphans

    def __init__(self, *, armed=False, orders=1, reconciled=True):
        self.transport = _Transport(armed)
        self.store = _Store(orders)
        self.reconciled = reconciled
        self.last_orphan_sweep_t = 0.0
        self.polled = 0

    def poll_orders(self):
        self.polled += 1
        return []


@pytest.fixture
def cancels(monkeypatch):
    seen = {}

    def fake(store, transport, *, reason):
        seen["reason"] = reason
        seen["n"] = seen.get("n", 0) + 1
        return {"cancelled": ["venue-1"], "still_open": [], "unverified": [],
                "no_venue_id": [], "errors": [], "filled": []}

    monkeypatch.setattr(LX, "deadman", fake)
    return seen


def test_an_unarmed_process_withdraws_an_order_it_owns(cancels):
    r = _Runner(armed=False, orders=1)
    rec = R.Runner.maybe_cancel_orphans(r, 1000.0)
    assert rec["cancelled"] == ["venue-1"]
    assert cancels["reason"] == "orphan_unarmed"


def test_the_armed_paths_still_own_cancellation(cancels):
    """An armed process has the window-end, expiry and halt sweeps."""
    r = _Runner(armed=True, orders=1)
    assert R.Runner.maybe_cancel_orphans(r, 1000.0) is None
    assert cancels == {}


def test_nothing_resting_cancels_nothing(cancels):
    r = _Runner(armed=False, orders=0)
    assert R.Runner.maybe_cancel_orphans(r, 1000.0) is None
    assert cancels == {}


def test_an_unconfirmed_book_is_never_cancelled_against(cancels):
    """Cancelling by an id we are not sure is current can lose a fill."""
    r = _Runner(armed=False, orders=1, reconciled=False)
    assert R.Runner.maybe_cancel_orphans(r, 1000.0) is None
    assert cancels == {}


def test_a_fill_is_booked_before_the_cancel(cancels):
    r = _Runner(armed=False, orders=1)
    R.Runner.maybe_cancel_orphans(r, 1000.0)
    assert r.polled == 1


def test_an_order_that_filled_since_the_last_poll_is_not_cancelled(cancels):
    """poll_orders may clear the book; then there is nothing to withdraw."""
    r = _Runner(armed=False, orders=1)

    def poll():
        r.polled += 1
        r.store.n = 0                 # the venue reported it terminal
        return [{"filled": True}]

    r.poll_orders = poll
    assert R.Runner.maybe_cancel_orphans(r, 1000.0) is None
    assert cancels == {}


def test_the_sweep_is_rate_limited(cancels):
    r = _Runner(armed=False, orders=1)
    R.Runner.maybe_cancel_orphans(r, 1000.0)
    R.Runner.maybe_cancel_orphans(r, 1005.0)
    assert cancels["n"] == 1


def test_the_sweep_retries_after_its_interval(cancels):
    r = _Runner(armed=False, orders=1)
    R.Runner.maybe_cancel_orphans(r, 1000.0)
    R.Runner.maybe_cancel_orphans(r, 1000.0 + r.ORPHAN_SWEEP_EVERY_S + 1)
    assert cancels["n"] == 2


def test_an_unverified_cancel_is_retried_rather_than_claimed(monkeypatch):
    """still_open and unverified are failures; deadman marks the store
    UNCERTAIN and the next sweep tries again."""
    calls = {"n": 0}

    def fake(store, transport, *, reason):
        calls["n"] += 1
        store.mark_uncertain("cancel unverified")
        return {"cancelled": [], "still_open": [], "unverified": ["venue-9"],
                "no_venue_id": [], "errors": [], "filled": []}

    monkeypatch.setattr(LX, "deadman", fake)
    r = _Runner(armed=False, orders=1)
    rec = R.Runner.maybe_cancel_orphans(r, 1000.0)
    assert rec["unverified"] == ["venue-9"]
    assert r.store.uncertain
    R.Runner.maybe_cancel_orphans(r, 1000.0 + r.ORPHAN_SWEEP_EVERY_S + 1)
    assert calls["n"] == 2


def test_only_orders_in_our_own_ledger_are_touched():
    """`deadman` iterates the ledger's live_orders, so a manual order the
    owner placed -- which this book never recorded -- is never cancelled."""
    import inspect
    src = inspect.getsource(LX.deadman)
    assert "store.live_orders()" in src


def test_cancellation_is_not_blocked_by_the_shadow_lock():
    assert "submit" in R.ShadowOnlyTransport.BLOCKED
    assert "cancel" not in R.ShadowOnlyTransport.BLOCKED


def test_the_sweep_runs_on_every_tick():
    import inspect
    src = inspect.getsource(R.Runner.tick)
    assert "maybe_cancel_orphans" in src

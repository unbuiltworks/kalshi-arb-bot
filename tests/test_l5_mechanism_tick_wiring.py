"""`Runner.mechanism_tick`: which of our own code paths are out of the way.

This is the load-bearing part of the mode and the easiest to get wrong, so it
is pinned by calling the REAL method with recording stubs rather than by
reading the source.

THE POINT. Three of our own paths would withdraw the test order before the
watchdog could: `maybe_cancel_orphans` (which runs precisely when NOT armed),
`maybe_ts_deadline` (the executor's own cancel margin), and the halt branch's
`LX.deadman`. The last is the worst, because the watchdog ASSERTS THE LATCH
BEFORE IT CANCELS -- so under the ordinary tick the executor would read that
latch and race the cancel it is meant to be observing.

What must NOT be out of the way: the halt's DISARM (an automatic halt still
has to stop submissions) and reconciliation (a fill while resting must be
booked with its cost basis).
"""
from __future__ import annotations

from src import l5_pmus_live_runner as R

#: every canceller that must not touch the one resting test order
CANCELLERS = ("maybe_cancel_orphans", "maybe_ts_deadline",
              "maybe_ts_completion", "maybe_qm_deadline")
#: THE STRATEGY ENTRY POINT. `poll` reads the L5 action feed and hands each
#: fresh action to `handle`, which builds an intent and submits it. This file
#: asserted the CANCELLERS were absent and never asserted the STRATEGY was --
#: so `self.poll(now)` sat in mechanism_tick through a live activation, and at
#: 18:34:36Z on 2026-10-18, with the shadow lock lifted, produced two
#: L5_LIVE_DECISION records (UP 0.06 @ 0.31, DOWN 0.9 @ 0.6) that only the
#: pending arming kept off the venue.
STRATEGY = ("poll", "handle")
#: reconciliation and safety that must still run
PRESERVED = ("roll", "maybe_expire_authorization", "maybe_preflight",
             "sweep_settlements", "reconcile_if_needed")


class Transport:
    def __init__(self, armed=True):
        self.armed = armed
        self.disarms = []
        self.market_slug = "cpc-btc-updown-15m-2026-10-18-1800z"

    def disarm(self, why):
        self.disarms.append(why)
        self.armed = False


class Store:
    def __init__(self, live=1):
        self._live = live

    def live_order_count(self):
        return self._live


class Mech:
    state = "RESTING"
    venue_order_id = "D4MECH0000ZZ1"

    def __init__(self):
        self.steps = []

    def step(self, now):
        self.steps.append(now)
        return {}


class Stub:
    """A Runner-shaped recorder. `mechanism_tick` is called unbound on it, so
    the method under test is the real one."""

    def __init__(self, *, halted=False, cooling=0.0, live=1, armed=True):
        self.calls = []
        self._halted = halted
        self._cooling = cooling
        self.transport = Transport(armed=armed)
        self.store = Store(live=live)
        self.stats = __import__("collections").Counter()
        self.now_t = None
        for name in (CANCELLERS + PRESERVED + STRATEGY
                     + ("poll_orders", "resolve_pending_terminal")):
            self._install(name)

    def _install(self, name):
        def rec(*a, **kw):
            self.calls.append(name)
        setattr(self, name, rec)

    def halted(self):
        return self._halted

    def venue_cooling(self):
        return self._cooling


def run(stub, mech=None, now=1792368060.0):
    mech = mech or Mech()
    R.Runner.mechanism_tick(stub, now, mech)
    return mech


# ------------------------------------------------- the cancellers are absent

def test_no_canceller_of_ours_runs_during_the_mechanism(monkeypatch):
    stub = Stub()
    run(stub)
    for name in CANCELLERS:
        assert name not in stub.calls, name


def test_no_canceller_runs_even_when_halted(monkeypatch):
    """The halt is exactly when `maybe_cancel_orphans` would fire, and
    exactly when the watchdog is mid-cancel. This is the race."""
    called = []
    monkeypatch.setattr(R.LX, "deadman",
                        lambda *a, **kw: called.append("deadman") or {})
    stub = Stub(halted=True)
    run(stub)
    for name in CANCELLERS:
        assert name not in stub.calls, name
    assert called == [], "the halt branch called deadman and raced the watchdog"


def test_the_halt_still_disarms_so_nothing_further_can_be_submitted(
        monkeypatch):
    monkeypatch.setattr(R.LX, "deadman", lambda *a, **kw: {})
    stub = Stub(halted=True)
    run(stub)
    assert stub.transport.disarms == ["operator_halt"]
    assert stub.transport.armed is False


def test_an_unhalted_run_is_not_disarmed(monkeypatch):
    stub = Stub(halted=False)
    run(stub)
    assert stub.transport.disarms == []


# ---------------------------------------------------- what is preserved

def test_reconciliation_still_runs_so_a_fill_is_booked(monkeypatch):
    stub = Stub(live=1)
    run(stub)
    assert "poll_orders" in stub.calls
    assert "resolve_pending_terminal" in stub.calls
    assert "sweep_settlements" in stub.calls
    assert "reconcile_if_needed" in stub.calls


def test_preflight_and_the_authorization_expiry_still_run(monkeypatch):
    stub = Stub()
    run(stub)
    for name in ("roll", "maybe_expire_authorization", "maybe_preflight"):
        assert name in stub.calls, name


# ------------------------------------------- the STRATEGY is absent

def test_the_strategy_entry_point_never_runs(monkeypatch):
    """THE BUG THIS FILE MISSED. `poll` -> `handle` is the strategy, and it
    was running under the mode that claims to replace it."""
    stub = Stub()
    run(stub)
    for name in STRATEGY:
        assert name not in stub.calls, (
            "%s ran: the L5 strategy is live under the mechanism mode" % name)


def test_the_strategy_never_runs_even_in_a_venue_cooloff(monkeypatch):
    """The cool-off branch had its own `self.poll(now)` call."""
    stub = Stub(cooling=5.0)
    run(stub)
    for name in STRATEGY:
        assert name not in stub.calls, name


def test_the_strategy_never_runs_even_when_halted(monkeypatch):
    monkeypatch.setattr(R.LX, "deadman", lambda *a, **kw: {})
    stub = Stub(halted=True)
    run(stub)
    for name in STRATEGY:
        assert name not in stub.calls, name


def test_the_ordinary_tick_DOES_run_the_strategy(monkeypatch):
    """The positive control: `poll` belongs in `tick` and must stay there, or
    this test would pass against a runner that had simply lost the ability to
    trade at all."""
    import inspect
    src = inspect.getsource(R.Runner.tick)
    assert "self.poll(now)" in src


def test_order_polling_is_skipped_when_nothing_is_live(monkeypatch):
    stub = Stub(live=0)
    run(stub)
    assert "poll_orders" not in stub.calls


# --------------------------------------------------------------- the driver

def test_the_mechanism_is_stepped_once_per_tick(monkeypatch):
    stub = Stub()
    mech = run(stub)
    assert mech.steps == [1792368060.0]


def test_a_venue_cooloff_skips_the_mechanism_step(monkeypatch):
    """Spending the rate budget during a cool-off is what the cool-off is
    for; the order is already resting and the watchdog is unaffected."""
    stub = Stub(cooling=5.0)
    mech = run(stub)
    assert mech.steps == []
    assert "maybe_preflight" not in stub.calls
    # and NOT poll: the cool-off branch used to call it, which is how the
    # strategy ran under this mode. Nothing is polled here at all now.
    assert "poll" not in stub.calls


def test_the_tick_stamps_the_time_like_the_ordinary_one(monkeypatch):
    stub = Stub()
    run(stub, now=123.0)
    assert stub.now_t == 123.0


# ------------------------------------------------- the mode is exclusive

def test_the_mode_replaces_the_trading_tick_rather_than_supplementing_it():
    """Both ticks must never run in the same pass: `tick` contains the
    cancellers this mode exists to keep away from the order."""
    import inspect
    src = inspect.getsource(R.main)
    assert "runner.mechanism_tick(now, mech)" in src
    assert "else:\n                runner.tick(now)" in src


def test_the_mode_defaults_off(monkeypatch):
    monkeypatch.delenv(R.MECH.MODE_ENV, raising=False)
    assert R.MECH.enabled() is False


# ------------------------------------------------------------- packaging

def _copied(path="deploy/l5_pmus_live/Dockerfile"):
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, path)) as fh:
        return [ln.split()[1] for ln in fh if ln.startswith("COPY ")]


def test_the_mechanism_module_is_in_the_executor_image():
    """A NARROW packaging check, and it exists because the broad one is red.

    `test_l5_live_runner_graft.py` walks the whole import closure and would
    in principle catch this, but it currently fails on unrelated
    `config/cost_basis_batch_manifest_*.json` entries -- its first assertion
    trips before it ever reaches the module list, so it offered no protection
    at all here. The runner imports this module EAGERLY, so an image without
    it does not degrade: it crashloops the money-capable service on boot.
    """
    assert "src/l5_cancel_mechanism.py" in _copied()


def test_every_module_the_mechanism_itself_imports_is_in_the_image():
    """Including the ones it imports lazily, inside functions."""
    copied = set(_copied())
    for mod in ("src/l5_order_registry_server.py", "src/l5_order_body.py",
                "src/l5_pmus_transport.py", "src/l5_live_execution.py"):
        assert mod in copied, mod

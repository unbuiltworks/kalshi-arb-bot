"""A transient GET must not cost the session.

LIVE INCIDENT, 2026-09-26T16:12:06Z. Three order-progress reads returned
HTTPError. poll_orders marked the book uncertain -- correctly; an order whose
state cannot be read might be live and consuming the envelope. The
SAFETY_OVERLAY then refused every action ORDER_STATE_UNCERTAIN and
submissions stopped dead at 43 while the service looked perfectly healthy:
armed true, preflight PASSED, feed connected, no errors.

Uncertainty is cleared ONLY by a full authoritative reconciliation, and
reconcile ran once per process behind a flag. Nothing in the process could
clear it. The executor was latched shut until a redeploy.

These tests hold the recovery to the shape the owner specified: no
submissions while uncertain, clearing only via a real venue reconciliation,
fail-closed and retry when that reconciliation fails, adopt rather than
duplicate live orders, and never reset realized accounting to recover.
"""
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R


class FakeTransport:
    """Only what the runner asks of it."""
    def __init__(self, positions=None, open_orders=None, raises=None):
        self.market_slug = "cpc-btc-updown-15m-2026-09-26-1600z"
        self.armed = True
        self._positions = positions if positions is not None else []
        self._open = open_orders if open_orders is not None else []
        self.raises = raises
        self.calls = 0

    def positions(self):
        self.calls += 1
        if self.raises:
            raise self.raises
        return list(self._positions)

    def open_orders(self):
        if self.raises:
            raise self.raises
        return list(self._open)

    def settlement(self, slug):
        return {"market": slug, "state": "", "settlement_px": None,
                "terminal": False}


def make_runner(tmp, transport):
    r = R.Runner.__new__(R.Runner)          # no venue work in __init__
    r.store = LX.InventoryStore(os.path.join(tmp, "inv.json"))
    r.limits = LX.SafetyLimits(**R.PROBE_LIMITS)
    r.transport = transport
    r.reconciled = False
    r.last_reconcile_t = 0.0
    r.last_progress_t = 0.0
    r.now_t = 1000.0
    import collections
    r.stats = collections.Counter()
    r.win = collections.Counter()
    return r


@pytest.fixture()
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield d


# ------------------------------------------------- uncertainty stops trading

def test_while_uncertain_the_overlay_refuses_every_submission():
    lim = LX.SafetyLimits(**R.PROBE_LIMITS)
    kw = dict(price=D("0.5"), qty=D("1"), market="m", committed_by_market={},
              residual_after=D(0), realized_today=D(0), open_markets=[],
              outstanding_by_market={}, outstanding_residual=D(0),
              live_order_count=0, risk_by_market={})
    assert lim.refuse(orders_uncertain=None, **kw) is None
    assert lim.refuse(orders_uncertain="order query raised HTTPError",
                      **kw) == "ORDER_STATE_UNCERTAIN"


def test_uncertainty_outranks_every_other_gate():
    """It is checked first, so it cannot be masked by a laxer limit."""
    lim = LX.SafetyLimits(**R.PROBE_LIMITS)
    assert lim.refuse(
        price=D("0.5"), qty=D("1"), market="m", committed_by_market={},
        residual_after=D(0), realized_today=D(0), open_markets=[],
        outstanding_by_market={}, outstanding_residual=D(0),
        live_order_count=99,                     # would be MAX_LIVE_ORDERS
        risk_by_market={}, orders_uncertain="x") == "ORDER_STATE_UNCERTAIN"


# ------------------------------------- the round trip the incident needed

def test_transient_failure_latches_then_a_real_reconcile_clears_it(tmp):
    """progress failure -> uncertain -> no submissions -> authoritative
    reconcile -> cleared -> trading may resume."""
    t = FakeTransport()
    r = make_runner(tmp, t)

    # a first, clean reconcile: the book is confirmed
    r.reconcile_if_needed(r.now_t)
    assert r.reconciled is True
    assert r.store.orders_uncertain() is None

    # the incident: a progress read raises and the book goes uncertain
    r.store.mark_uncertain("order query raised HTTPError for l5f-abc")
    assert r.store.orders_uncertain()

    # ...and nothing may be submitted while that holds
    assert r.limits.refuse(
        price=D("0.5"), qty=D("1"), market=t.market_slug,
        committed_by_market={}, residual_after=D(0), realized_today=D(0),
        open_markets=[], outstanding_by_market={}, outstanding_residual=D(0),
        live_order_count=0, risk_by_market={},
        orders_uncertain=r.store.orders_uncertain()) == "ORDER_STATE_UNCERTAIN"

    # the OLD behaviour: reconciled is already True, so a once-per-process
    # guard would return here and the flag would never clear again.
    r.now_t += R.RECONCILE_RETRY_S + 1
    r.reconcile_if_needed(r.now_t)

    assert r.store.orders_uncertain() is None      # cleared
    assert r.limits.refuse(
        price=D("0.5"), qty=D("1"), market=t.market_slug,
        committed_by_market={}, residual_after=D(0), realized_today=D(0),
        open_markets=[], outstanding_by_market={}, outstanding_residual=D(0),
        live_order_count=0, risk_by_market={},
        orders_uncertain=r.store.orders_uncertain()) is None


def test_a_failed_reconcile_stays_latched(tmp):
    t = FakeTransport(raises=RuntimeError("HTTPError"))
    r = make_runner(tmp, t)
    r.store.mark_uncertain("progress read failed")
    r.now_t += R.RECONCILE_RETRY_S + 1
    r.reconcile_if_needed(r.now_t)
    assert r.store.orders_uncertain() is not None
    assert r.reconciled is False


def test_a_succeeding_get_alone_never_clears_uncertainty(tmp):
    """Only reconcile_from_venue clears the flag -- not a read that happens
    to work."""
    t = FakeTransport()
    r = make_runner(tmp, t)
    r.store.mark_uncertain("progress read failed")
    assert t.positions() == []                  # a GET succeeds...
    assert r.store.orders_uncertain() is not None   # ...and clears nothing


def test_the_retry_is_rate_limited_not_every_tick(tmp):
    """Hammering a venue that is already erroring turns a fault into a rate
    limit."""
    t = FakeTransport(raises=RuntimeError("HTTPError"))
    r = make_runner(tmp, t)
    r.store.mark_uncertain("x")
    r.now_t = 10_000.0
    r.reconcile_if_needed(r.now_t)
    first = t.calls
    for _ in range(5):                       # same second, repeatedly
        r.reconcile_if_needed(r.now_t)
    assert t.calls == first
    r.now_t += R.RECONCILE_RETRY_S + 1
    r.reconcile_if_needed(r.now_t)
    assert t.calls == first + 1


# ----------------------------------------------------- orders, not duplicates

def test_a_live_venue_order_is_adopted_under_its_own_id_not_duplicated(tmp):
    t = FakeTransport(open_orders=[{
        "client_order_id": "l5f-deadbeef", "market": "m", "side": "UP",
        "price": {"value": "0.55", "currency": "USD"},
        "qty": "2.25", "remaining_qty": "2.25"}])
    r = make_runner(tmp, t)
    r.reconcile_if_needed(r.now_t)
    live = r.store.live_orders()
    assert list(live) == ["l5f-deadbeef"]
    assert live["l5f-deadbeef"]["state"] == "LIVE_AT_VENUE"
    assert live["l5f-deadbeef"]["price"] == "0.55"


def test_an_order_the_venue_does_not_list_is_released(tmp):
    t = FakeTransport(open_orders=[])
    r = make_runner(tmp, t)
    r.store.state.setdefault("live_orders", {})["l5f-gone"] = {
        "market": "m", "side": "UP", "price": "0.5", "qty": "1"}
    r.reconcile_if_needed(r.now_t)
    assert r.store.live_orders() == {}


def test_client_order_ids_are_derived_so_a_resubmit_cannot_double_up():
    """The idempotency guarantee adoption rests on."""
    a = LX.client_order_id("action|key|1")
    b = LX.client_order_id("action|key|1")
    assert a == b
    assert a != LX.client_order_id("action|key|2")


# ------------------------------------------- accounting survives recovery

def test_recovery_does_not_reset_realized_accounting(tmp):
    """The -$5 halt is cumulative. Clearing uncertainty must not clear it."""
    t = FakeTransport()
    r = make_runner(tmp, t)
    r.store.state["realized_today"] = "-3.25"
    r.store.save()
    r.store.mark_uncertain("x")
    r.now_t += R.RECONCILE_RETRY_S + 1
    r.reconcile_if_needed(r.now_t)
    assert r.store.orders_uncertain() is None
    assert r.store.realized_today() == D("-3.25")


def test_the_halt_still_binds_after_a_recovery(tmp):
    t = FakeTransport()
    r = make_runner(tmp, t)
    r.store.state["realized_today"] = "-5.00"
    r.store.save()
    r.store.mark_uncertain("x")
    r.now_t += R.RECONCILE_RETRY_S + 1
    r.reconcile_if_needed(r.now_t)
    assert r.limits.refuse(
        price=D("0.5"), qty=D("1"), market="m", committed_by_market={},
        residual_after=D(0), realized_today=r.store.realized_today(),
        open_markets=[], outstanding_by_market={}, outstanding_residual=D(0),
        live_order_count=0, risk_by_market={},
        orders_uncertain=None) == "DAILY_REALIZED_LOSS_HALT"


def test_realized_survives_a_restart_because_the_store_is_on_the_volume(tmp):
    path = os.path.join(tmp, "inv.json")
    s1 = LX.InventoryStore(path)
    s1.state["realized_today"] = "-1.75"
    s1.save()
    assert LX.InventoryStore(path).realized_today() == D("-1.75")


# -------------------------------------------- phantom / stalled live orders

def test_live_orders_that_stop_progressing_force_a_reconcile(tmp):
    """1700z: eight orders the venue had finished with, which we never
    learned. No error was raised, so nothing set uncertainty; they simply
    stopped changing while holding every slot in MAX_LIVE_ORDERS, and 276
    actions were refused against orders that did not exist."""
    t = FakeTransport(open_orders=[])          # the venue has none of them
    r = make_runner(tmp, t)
    r.reconcile_if_needed(r.now_t)             # a first, clean confirmation
    assert r.store.orders_uncertain() is None

    live = r.store.state.setdefault("live_orders", {})
    for i in range(8):
        live["l5f-phantom%d" % i] = {"market": t.market_slug, "side": "UP",
                                     "price": "0.5", "qty": "1",
                                     "remaining": "1"}
    r.store.save()
    r.last_progress_t = r.now_t                # progress was moving...
    assert r.store.live_order_count() == 8

    r.now_t += R.STALLED_ORDER_S + 1           # ...and then stopped
    r.reconcile_if_needed(r.now_t)

    # the venue is the authority: all eight released, nothing invented
    assert r.store.live_orders() == {}
    assert r.store.orders_uncertain() is None  # cleared by the reconcile
    assert r.store.position(t.market_slug, "UP") == D(0)


def test_a_stall_fails_closed_before_it_is_resolved(tmp):
    """While the state is unknown, no new order may be submitted."""
    t = FakeTransport(raises=RuntimeError("HTTPError"))
    r = make_runner(tmp, t)
    r.reconciled = True
    live = r.store.state.setdefault("live_orders", {})
    live["l5f-stuck"] = {"market": t.market_slug, "side": "UP",
                         "price": "0.5", "qty": "1", "remaining": "1"}
    r.store.save()
    r.last_progress_t = r.now_t
    r.now_t += R.STALLED_ORDER_S + 1
    r.reconcile_if_needed(r.now_t)
    assert r.store.orders_uncertain() is not None
    assert r.limits.refuse(
        price=D("0.5"), qty=D("1"), market=t.market_slug,
        committed_by_market={}, residual_after=D(0), realized_today=D(0),
        open_markets=[], outstanding_by_market={}, outstanding_residual=D(0),
        live_order_count=1, risk_by_market={},
        orders_uncertain=r.store.orders_uncertain()) == "ORDER_STATE_UNCERTAIN"


def test_a_disappeared_order_is_released_not_turned_into_a_fill(tmp):
    """Disappearance is not evidence of execution."""
    t = FakeTransport(open_orders=[])
    r = make_runner(tmp, t)
    r.store.state.setdefault("live_orders", {})["l5f-gone"] = {
        "market": "m", "side": "DOWN", "price": "0.4", "qty": "3",
        "remaining": "3"}
    r.store.save()
    r.reconcile_if_needed(r.now_t)
    assert r.store.live_orders() == {}
    assert r.store.position("m", "DOWN") == D(0)
    assert r.store.leg_cost("m")["DOWN"] == D(0)
    assert r.store.realized_today() == D(0)


def test_progress_that_keeps_moving_never_trips_the_stall(tmp):
    t = FakeTransport()
    r = make_runner(tmp, t)
    r.store.state.setdefault("live_orders", {})["l5f-a"] = {
        "market": "m", "side": "UP", "price": "0.5", "qty": "1",
        "remaining": "1"}
    r.store.save()
    r.last_progress_t = r.now_t
    r.now_t += R.STALLED_ORDER_S - 1
    assert r.orders_stalled_for_s(r.now_t) < R.STALLED_ORDER_S


def test_no_live_orders_means_no_stall(tmp):
    t = FakeTransport()
    r = make_runner(tmp, t)
    r.now_t += 10_000
    assert r.orders_stalled_for_s(r.now_t) == 0.0


# --------------------------------------------------------- the tick ordering

def test_settlement_is_swept_before_reconciliation():
    """A settled market has no venue position left to read, so our book is
    the only record of its economics. Reconciling first would settle an
    emptied book at zero and erase a real result."""
    import ast
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "l5_pmus_live_runner.py")).read()
    tree = ast.parse(src)
    tick = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "tick"][0]
    calls = [n.func.attr for n in ast.walk(tick)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert "sweep_settlements" in calls and "reconcile_if_needed" in calls
    assert calls.index("sweep_settlements") < calls.index("reconcile_if_needed")

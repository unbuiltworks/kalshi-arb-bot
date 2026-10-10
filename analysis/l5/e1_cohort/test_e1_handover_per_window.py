"""ONE HANDOVER PER WINDOW, NOT ONE PER RUN.

THE DEFECT. `lifecycle.deadman_result` is set once and never cleared, and
`run_cohort` guarded the handover with

    if past_cutoff and not lifecycle.deadman_result:

so the handover fired ONCE FOR THE WHOLE RUN. In a twelve-window probe that
cancels window 0 and then never runs again: an order resting at the end of
windows 1..11 is never cancelled by the engine, and the only thing that
would notice is the read-only monitor, which cannot cancel. Every probe so
far has been single-window, which is exactly why this stayed invisible.

These tests drive the real lifecycle across two windows.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import pair_lifecycle as PLC
from analysis.l5.e1_cohort.test_e1_pair_lifecycle import FakeVenue, SLUG, UP
from analysis.l5.live_exec2 import orders as O


def _cancelling(venue):
    class T:
        def request(self, method, path, body=None, base=None):
            if path.startswith("/v1/order/") and path.endswith("/cancel"):
                oid = path.split("/")[3]
                if oid in venue.orders and \
                        D(venue.orders[oid]["leavesQuantity"]) > 0:
                    venue.cancel(oid)
                    return {"canceledOrderIds": [oid]}
                return {"canceledOrderIds": []}
            if path == O.DEADMAN_PATH:
                raise AssertionError("must cancel by owned id, not by market")
            return {}
    return T


def _snap(venue, *, window, known):
    view = OS.read_order_state(venue.reader(), slugs=[SLUG], outcome_up=UP,
                              now=1000.0, known_ids=list(known),
                              roles={k: OS.FIRST_LEG for k in known})
    return {"market": SLUG, "window": window, "order_state": view,
            "known_order_ids": list(known)}


def test_each_window_gets_its_own_handover():
    """TWO WINDOWS, TWO ORDERS, TWO CANCELS. Before the fix the second
    order was left resting at the venue."""
    v = FakeVenue()
    lc = PLC.PairLifecycle()

    # ---- window 0 -------------------------------------------------------
    v.rest("W0-1", side=UP, wire="0.49", qty="0.13")
    lc.record_first_leg(order_id="W0-1", window=0, slot=0, price="0.49")
    dm0 = lc.deadman(snapshot=_snap(v, window=0, known=["W0-1"]), now=1000.0,
                     transport_factory=_cancelling(v), orders_module=O,
                     owned_ids=["W0-1"],
                     read_order=lambda oid: v.reader()(OS.ORDER_PATH % oid))
    assert dm0["ran"] is True and dm0["all_cancelled"] is True
    assert v.orders["W0-1"]["state"] == "ORDER_STATE_CANCELED"
    assert 0 in lc.deadman_by_window

    # ---- window 1: a NEW order, and the handover must run again ---------
    v.rest("W1-1", side=UP, wire="0.51", qty="0.13")
    lc.record_first_leg(order_id="W1-1", window=1, slot=0, price="0.51")
    assert 1 not in lc.deadman_by_window, (
        "window 1 must not inherit window 0's handover")
    dm1 = lc.deadman(snapshot=_snap(v, window=1, known=["W0-1", "W1-1"]),
                     now=2000.0, transport_factory=_cancelling(v),
                     orders_module=O, owned_ids=["W0-1", "W1-1"],
                     read_order=lambda oid: v.reader()(OS.ORDER_PATH % oid))
    assert dm1["ran"] is True
    assert "W1-1" in dm1["targets"]
    assert v.orders["W1-1"]["state"] == "ORDER_STATE_CANCELED", (
        "the second window's order must be cancelled too")
    assert dm1["all_cancelled"] is True
    assert set(lc.deadman_by_window) == {0, 1}


def test_the_old_guard_would_have_skipped_the_second_window():
    """THE REGRESSION, STATED AS A CONDITION. `deadman_result` is truthy
    after the first handover, so any guard reading it skips every later
    window. The per-window map is what the guard must read."""
    v = FakeVenue()
    lc = PLC.PairLifecycle()
    v.rest("W0-1", side=UP, wire="0.49", qty="0.13")
    lc.record_first_leg(order_id="W0-1", window=0, slot=0, price="0.49")
    lc.deadman(snapshot=_snap(v, window=0, known=["W0-1"]), now=1000.0,
               transport_factory=_cancelling(v), orders_module=O,
               owned_ids=["W0-1"],
               read_order=lambda oid: v.reader()(OS.ORDER_PATH % oid))
    assert lc.deadman_result is not None       # the old guard: truthy
    assert 1 not in lc.deadman_by_window       # the new guard: still open
    assert bool(lc.deadman_result) and 1 not in lc.deadman_by_window


def test_a_refused_handover_is_also_recorded_per_window():
    """A refusal must occupy the window's slot too, so a refused handover
    is not silently retried on every iteration -- and is still visible."""
    v = FakeVenue()
    lc = PLC.PairLifecycle()
    v.rest("W0-1", side=UP, wire="0.49", qty="0.13")
    lc.record_first_leg(order_id="W0-1", window=0, slot=0, price="0.49")
    dm = lc.deadman(snapshot={"market": SLUG, "window": 0,
                              "order_state": None},
                    now=1000.0, transport_factory=_cancelling(v),
                    orders_module=O, owned_ids=["W0-1"])
    assert dm["ran"] is False
    assert dm["because"] == "ORDER_STATE_NOT_ESTABLISHED"
    assert 0 in lc.deadman_by_window
    assert v.orders["W0-1"]["state"] == "ORDER_STATE_NEW"

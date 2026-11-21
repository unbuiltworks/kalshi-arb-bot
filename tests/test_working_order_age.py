"""An order's resting age must survive authoritative reconciliation.

THE DEFECT. `WorkingOrders.reconcile` rebuilt the working set from the
venue's open-order list and read `resting_since` OFF THE VENUE ROW:

    "resting_since": float(o.get("resting_since") or now),

`resting_since` is our own bookkeeping key and no venue row carries it, so
the fallback was taken every single cycle and every resting order was
re-stamped brand new. Measured on COHORT-2: an order that truly rested 473s
(createdAt 12:50:09.881 -> updatedAt 12:58:02.681) was computed at
0.15-0.56s old at ~60 consecutive cycles. `reconcile_surface` retains
anything younger than STALE_ORDER_S and marks the leg occupied, so the 30s
rest floor became an INFINITE floor: no resting order could ever be replaced
for price movement, in any window, and 58 of 132 action slots were forfeited
while a complement sat 30 ticks stale.

THIS SUITE IS THE REGRESSION. Each test names the property the fix restores,
and the properties are the ones the owner enumerated. Nothing here widens a
bound: the rest floor, the working-order cap and the envelope are untouched.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import ws_continuous as WS


def _venue(coid, leg="NO", price="0.29", qty="1", oid="145828836352438305",
           created=None):
    row = {"clientOrderId": coid, "orderId": oid, "outcome": leg.lower(),
           "price": price, "quantity": qty}
    if created is not None:
        row["createdAt"] = created
    return row


def _w(price="0.29", leg="NO", qty="1", since=0.0):
    return {"leg": leg, "price": price, "qty": qty, "resting_since": since,
            "client_order_id": "c", "venue_order_id": "1"}


# -- the property that was broken -------------------------------------------
def test_the_original_age_survives_repeated_reconciliations():
    """THE REGRESSION. Sixty reconciles must not make a 473s order young."""
    w = WS.WorkingOrders()
    w.add("c1", leg="NO", price="0.29", qty="1", now=1000.0,
          venue_order_id="145828836352438305")
    for tick in range(60):
        w.reconcile([_venue("c1")], 1000.0 + 8 * tick)
    assert w.by_id["c1"]["resting_since"] == 1000.0
    age = 1473.0 - w.by_id["c1"]["resting_since"]
    assert age == pytest.approx(473.0)


def test_an_order_can_never_get_younger():
    w = WS.WorkingOrders()
    w.add("c1", leg="NO", price="0.29", qty="1", now=1000.0,
          venue_order_id="1")
    # a venue row claiming a LATER start must not win
    w.reconcile([_venue("c1", created="2030-01-01T00:00:00Z")], 2000.0)
    assert w.by_id["c1"]["resting_since"] == 1000.0


def test_a_restart_dates_the_order_from_the_venue_not_from_now():
    """We hold no prior record, so `createdAt` is the only honest source."""
    w = WS.WorkingOrders()
    w.reconcile([_venue("c1", created="2026-09-22T12:50:09.881Z")],
                1790081948.0)
    since = w.by_id["c1"]["resting_since"]
    assert since is not None
    assert 1790081948.0 - since == pytest.approx(538.1, abs=1.0)


def test_an_undatable_order_is_UNKNOWN_and_never_zero():
    """UNKNOWN IS NOT NOW. Calling it `now` is precisely the defect."""
    w = WS.WorkingOrders()
    w.reconcile([_venue("c1")], 5000.0)
    assert w.by_id["c1"]["resting_since"] is None


def test_a_partial_fill_keeps_the_remaining_order_s_age_and_updates_its_size():
    w = WS.WorkingOrders()
    w.add("c1", leg="NO", price="0.29", qty="4", now=1000.0,
          venue_order_id="1")
    w.reconcile([_venue("c1", qty="3")], 1100.0)      # one contract filled
    assert w.by_id["c1"]["resting_since"] == 1000.0   # queue position kept
    assert w.by_id["c1"]["qty"] == "3"


def test_a_genuinely_new_order_is_new():
    w = WS.WorkingOrders()
    w.add("c1", leg="NO", price="0.29", qty="1", now=1000.0,
          venue_order_id="1")
    w.reconcile([_venue("c1"), _venue("c2", leg="YES", price="0.39")], 1100.0)
    assert w.by_id["c1"]["resting_since"] == 1000.0
    assert w.by_id["c2"]["resting_since"] is None     # undatable, not 1100


# -- what the restored age is FOR -------------------------------------------
def test_an_order_below_the_floor_is_still_protected():
    plan = WS.reconcile_surface(
        [{"leg": "NO", "price": "0.45", "qty": "1"}],
        _Working({"c1": _w(since=1000.0)}), now=1029.0,
        cohort_prefix="GEMI-BTC15M")
    assert not plan["cancel"]
    assert plan["retain"] and plan["hold"]


def test_an_order_past_the_floor_becomes_replaceable():
    plan = WS.reconcile_surface(
        [{"leg": "NO", "price": "0.45", "qty": "1"}],
        _Working({"c1": _w(since=1000.0)}), now=1031.0,
        cohort_prefix="GEMI-BTC15M")
    assert len(plan["cancel"]) == 1
    assert plan["add"]


def test_the_floor_is_exactly_the_frozen_lane_s_and_was_not_widened():
    assert CP.STALE_ORDER_S == 30.0


def test_an_unknown_age_does_not_protect_the_order():
    """The same direction `reconcile_surface` already took with no clock."""
    plan = WS.reconcile_surface(
        [{"leg": "NO", "price": "0.45", "qty": "1"}],
        _Working({"c1": _w(since=None)}), now=1031.0,
        cohort_prefix="GEMI-BTC15M")
    assert len(plan["cancel"]) == 1


def test_the_fix_adds_no_working_order_capacity():
    """Two rungs desired on one leg must still not become three resting."""
    plan = WS.reconcile_surface(
        [{"leg": "NO", "price": "0.45", "qty": "1"}],
        _Working({"c1": _w(price="0.29", since=1000.0)}), now=1000.5,
        cohort_prefix="GEMI-BTC15M")
    assert len(plan["add"]) == 0 and len(plan["hold"]) == 1


def test_unreadable_open_orders_is_still_not_an_empty_book():
    w = WS.WorkingOrders()
    w.add("c1", leg="NO", price="0.29", qty="1", now=1000.0,
          venue_order_id="1")
    with pytest.raises(WS.ReconcileRequired):
        w.reconcile(None, 1100.0)
    assert "c1" in w.by_id


class _Working:
    """Minimal stand-in exposing the one method reconcile_surface uses."""

    def __init__(self, by_id):
        self.by_id = by_id

    def rungs(self):
        return list(self.by_id.values())

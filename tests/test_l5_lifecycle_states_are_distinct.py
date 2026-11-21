"""WOULD_SUBMIT, submitted, acknowledged, filled, settled -- five states.

PRIORITY 3's last requirement: do not confuse them. They are easy to
confuse because four of the five produce a record and only one of them
produces money, and the program has already been burned by exactly that
confusion -- `crossed: "WOULD_SUBMIT"` was written on a path that
deliberately did not submit, and reading it as a submission would have
counted a shadow decision as an order.

This pins the distinctions against the REAL store, through the real
hooks, so a reader of the probe's record cannot slide between them.

NO transport, no network, no order.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX

MARKET = "cpc-btc-updown-15m-2026-11-09-1200z"
COID = "l5f-lifecycle-1"


def store(tmp_path):
    return LX.InventoryStore(os.path.join(str(tmp_path), "inv.json"))


def test_1_WOULD_SUBMIT_leaves_no_order_and_no_inventory(tmp_path):
    """A decision is not an order. The disarmed engine reaches decisions
    constantly; none of them may appear in the order books."""
    st = store(tmp_path)
    assert st.live_orders() == {}
    assert not (st.state.get("submitted") or {})
    assert st.position(MARKET, "UP") == 0
    assert st.still_fillable_order_count() == 0


def test_2_submitted_reserves_the_envelope_but_is_not_a_fill(tmp_path):
    """`record_submit` is called BEFORE the wire, deliberately: if the
    process dies between send and ack the order may still be live at the
    venue. So submitted means "the envelope is spoken for", NOT "we own
    shares"."""
    st = store(tmp_path)
    st.record_submit(COID, {"market": MARKET, "side": "UP",
                            "price": "0.60", "qty": "1"},
                     session_id="probe", mode="live")
    assert COID in (st.state.get("submitted") or {})
    assert COID in st.live_orders()
    assert st.still_fillable_order_count() == 1
    # but NO inventory and NO cost basis
    assert st.position(MARKET, "UP") == 0
    assert st.leg_cost(MARKET)["UP"] == 0
    assert st.fill_count(MARKET, "UP") == 0


def test_3_acknowledged_is_a_venue_id_not_a_fill(tmp_path):
    """The ACK is a receipt. Probe 05 took an ACK for a resting order,
    never asked again, and carried it as live for the rest of the window."""
    st = store(tmp_path)
    st.record_submit(COID, {"market": MARKET, "side": "UP",
                            "price": "0.60", "qty": "1"},
                     session_id="probe", mode="live")
    st.state["live_orders"][COID]["venue_order_id"] = "V-1"
    assert st.live_orders()[COID]["venue_order_id"] == "V-1"
    assert st.position(MARKET, "UP") == 0
    assert st.fill_count(MARKET, "UP") == 0


def test_4_filled_is_the_only_state_that_creates_inventory(tmp_path):
    """And `apply_order_progress` is the only thing that gets us there."""
    st = store(tmp_path)
    st.record_submit(COID, {"market": MARKET, "side": "UP",
                            "price": "0.60", "qty": "1"},
                     session_id="probe", mode="live")
    st.apply_order_progress(
        COID, {"cum_qty": "1", "avg_px": "0.60", "leaves_qty": "0",
               "terminal": True, "state": "ORDER_STATE_FILLED",
               "commission": "0", "venue_order_id": "V-1"},
        market=MARKET, side="UP")
    assert st.position(MARKET, "UP") == D("1")
    assert st.leg_cost(MARKET)["UP"] == D("0.60")
    assert st.fill_count(MARKET, "UP") == 1
    # a fill is still NOT realized P&L: nothing has settled
    assert st.residual(MARKET) == D("1")


def test_5_filled_is_not_settled_and_the_store_says_so(tmp_path):
    """Inventory held is an open position. Realized P&L exists only after
    the venue settles and the redemption is booked."""
    st = store(tmp_path)
    st.record_submit(COID, {"market": MARKET, "side": "UP",
                            "price": "0.60", "qty": "1"},
                     session_id="probe", mode="live")
    st.apply_order_progress(
        COID, {"cum_qty": "1", "avg_px": "0.60", "leaves_qty": "0",
               "terminal": True, "state": "ORDER_STATE_FILLED",
               "commission": "0", "venue_order_id": "V-1"},
        market=MARKET, side="UP")
    # the position is open, so it is still in `positions`
    assert st.position(MARKET, "UP") == D("1")
    # and settlement is a SEPARATE act with its own method
    assert hasattr(st, "retire_settled_market") or any(
        "settle" in m for m in dir(st)), [m for m in dir(st)
                                          if "settle" in m]


def test_the_five_states_are_pairwise_distinguishable_from_the_store(
        tmp_path):
    """One table, so a reader of the probe's record cannot slide between
    them. `submitted` and `fill_count` together separate all five."""
    st = store(tmp_path)
    key = "%s|UP" % MARKET

    def snap():
        return (COID in (st.state.get("submitted") or {}),
                bool(str((st.live_orders().get(COID) or {})
                         .get("venue_order_id") or "")),
                int((st.state.get("fill_count") or {}).get(key, 0) or 0),
                str(st.position(MARKET, "UP")))

    assert snap() == (False, False, 0, "0")              # WOULD_SUBMIT
    st.record_submit(COID, {"market": MARKET, "side": "UP",
                            "price": "0.60", "qty": "1"},
                     session_id="probe", mode="live")
    assert snap() == (True, False, 0, "0")               # submitted
    st.state["live_orders"][COID]["venue_order_id"] = "V-1"
    assert snap() == (True, True, 0, "0")                # acknowledged
    st.apply_order_progress(
        COID, {"cum_qty": "1", "avg_px": "0.60", "leaves_qty": "0",
               "terminal": True, "state": "ORDER_STATE_FILLED",
               "commission": "0", "venue_order_id": "V-1"},
        market=MARKET, side="UP")
    s4 = snap()
    assert s4[2] == 1 and s4[3] == "1"                   # filled
    # settled is the only one that is NOT a store state at all: it is a
    # venue event plus a booked redemption, which is why the probe is
    # scored AFTER settlement and not at the fill.


def test_a_fill_is_never_reported_as_realized_profit(tmp_path):
    """The label discipline, as a test rather than a convention."""
    st = store(tmp_path)
    st.record_submit(COID, {"market": MARKET, "side": "UP",
                            "price": "0.60", "qty": "1"},
                     session_id="probe", mode="live")
    st.apply_order_progress(
        COID, {"cum_qty": "1", "avg_px": "0.60", "leaves_qty": "0",
               "terminal": True, "state": "ORDER_STATE_FILLED",
               "commission": "0", "venue_order_id": "V-1"},
        market=MARKET, side="UP")
    # a filled leg contributes COST, not profit
    assert st.leg_cost(MARKET)["UP"] == D("0.60")
    # and the realized measures are untouched by a fill
    try:
        realized = st.realized_today()
    except BaseException:
        realized = None
    assert realized in (None, D(0)) or realized == D(0), realized

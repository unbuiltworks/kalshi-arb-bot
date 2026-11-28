"""A venue order row that does not add up is not terminal truth.

THE CAPTURED FAILURE. On 2026-09-27 at 13:11:16Z `/v1/order/CREX7ZDP2YC6`
answered state ORDER_STATE_NEW, quantity 0.9, cumQuantity 0, leavesQuantity 0
for an order that had already FILLED 0.9 at 0.31 -- as the same endpoint
confirmed when the read-only recovery service asked it later. The old rule,
`terminal = leaves <= 0`, believed it: it booked a zero fill as final,
released the order, and logged that the figure came from the venue rather
than being assumed.

Both rows are checked in. The first must stay parked. The second must resolve.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "l5_1300z_incoherent_order_row.json"


@pytest.fixture(scope="module")
def rows():
    return json.loads(FIXTURE.read_text())


def test_the_captured_incoherent_row_is_refused(rows):
    row = rows["incoherent_as_served_at_1311z"]
    assert row["quantity"] == 0.9 and row["cumQuantity"] == 0 and row["leavesQuantity"] == 0
    with pytest.raises(EX.IncoherentOrderRow) as e:
        EX.order_progress(row)
    assert "does not add up" in str(e.value)
    assert "CREX7ZDP2YC6" in str(e.value)


def test_the_authoritative_filled_row_resolves_normally(rows):
    prog = EX.order_progress(rows["authoritative_as_served_later"])
    assert prog["cum_qty"] == D("0.9")
    assert prog["leaves_qty"] == D("0")
    assert prog["avg_px"] == D("0.3100")
    assert prog["commission"] == D("0")
    assert prog["terminal"] is True
    assert prog["state"] == "ORDER_STATE_FILLED"


def test_the_second_order_also_resolves_normally(rows):
    prog = EX.order_progress(rows["second_order_authoritative"])
    assert prog["cum_qty"] == D("0.9")
    assert prog["avg_px"] == D("0.2900")
    assert prog["terminal"] is True


def test_both_orders_together_are_the_reconciled_loss(rows):
    a = EX.order_progress(rows["authoritative_as_served_later"])
    b = EX.order_progress(rows["second_order_authoritative"])
    cost = a["cum_qty"] * a["avg_px"] + b["cum_qty"] * b["avg_px"]
    assert cost == D("0.540")          # settlementPx was 0.0000: all of it lost
    assert a["commission"] + b["commission"] == D("0")


# ------------------------------------------------- the rule, in isolation

@pytest.mark.parametrize("qty,cum,leaves,ok", [
    ("0.9", "0.9", "0", True),         # fully filled; numbers agree
    ("0.9", "0.4", "0.5", True),       # still working -- never retired here
    ("0.9", "0", "0.9", True),         # still working, untouched
    ("0.9", "0.9", "0.9", True),       # incoherent but leaves>0, so not retired
    ("0.9", "0", "0", False),          # THE CAPTURED 1300z FAILURE
    ("0.9", "0.5", "0", False),        # a vanished half must be SAID, not inferred
])
def test_the_coherence_rule(qty, cum, leaves, ok):
    """All rows here carry a NON-terminal state; that is the case the guard
    covers. Terminal states are exercised separately below."""
    row = {"id": "X", "state": "ORDER_STATE_NEW", "quantity": float(qty),
           "cumQuantity": float(cum), "leavesQuantity": float(leaves),
           "avgPx": {"value": "0.5"}, "commissionNotionalTotalCollected": {"value": "0"}}
    if ok:
        assert EX.order_progress(row)["cum_qty"] == D(cum)
    else:
        with pytest.raises(EX.IncoherentOrderRow):
            EX.order_progress(row)


@pytest.mark.parametrize("state", ["ORDER_STATE_CANCELLED", "ORDER_STATE_FILLED",
                                   "ORDER_STATE_REJECTED", "ORDER_STATE_EXPIRED"])
def test_a_terminal_state_explains_the_shortfall_and_is_allowed(state):
    """A cancelled order legitimately shows quantity 2.25, cum 0, leaves 0 --
    the rest was cancelled, not left working. The guard must not break it."""
    row = {"id": "X", "state": state, "quantity": 2.25, "cumQuantity": 0,
           "leavesQuantity": 0, "avgPx": {"value": "0"},
           "commissionNotionalTotalCollected": {"value": "0"}}
    prog = EX.order_progress(row)
    assert prog["terminal"] is True and prog["cum_qty"] == D("0")


def test_a_still_working_row_is_never_second_guessed():
    """leaves > 0 means nothing is being retired, so the guard stays out."""
    row = {"id": "X", "state": "ORDER_STATE_NEW", "quantity": 0.9,
           "cumQuantity": 0.9, "leavesQuantity": 0.9, "avgPx": {"value": "0.3"},
           "commissionNotionalTotalCollected": {"value": "0"}}
    assert EX.order_progress(row)["terminal"] is False


def test_a_row_without_quantity_is_not_second_guessed():
    """The check needs the venue to have sent the size. Without it there is
    nothing to compare against and the old behaviour stands."""
    row = {"id": "X", "state": "ORDER_STATE_FILLED", "cumQuantity": 2.25,
           "leavesQuantity": 0, "avgPx": {"value": "0.29"},
           "commissionNotionalTotalCollected": {"value": "0.03"}}
    prog = EX.order_progress(row)
    assert prog["terminal"] is True and prog["cum_qty"] == D("2.25")


def test_a_row_without_leaves_is_not_second_guessed():
    row = {"id": "X", "state": "ORDER_STATE_FILLED", "quantity": 2.25,
           "cumQuantity": 2.25, "avgPx": {"value": "0.29"},
           "commissionNotionalTotalCollected": {"value": "0"}}
    assert EX.order_progress(row)["terminal"] is True


def test_the_tolerance_is_tighter_than_the_venue_grid():
    """0.01 is the venue's quantity increment; the tolerance must be well
    inside it or a real one-increment discrepancy would pass."""
    assert EX.QUANTITY_COHERENCE_TOLERANCE < D("0.01")
    row = {"id": "X", "state": "ORDER_STATE_NEW", "quantity": 1.0, "cumQuantity": 0.99,
           "leavesQuantity": 0.0, "avgPx": {"value": "0.5"},
           "commissionNotionalTotalCollected": {"value": "0"}}
    with pytest.raises(EX.IncoherentOrderRow):
        EX.order_progress(row)


# --------------------------------- what the resolver does with the refusal

def test_the_refusal_reaches_the_parking_path_not_the_crash_path():
    """`resolve_pending_terminal` catches BaseException, re-raising only
    KeyboardInterrupt and SystemExit, then notes the attempt and leaves the
    order parked with the book uncertain. This must land in that handler."""
    assert issubclass(EX.IncoherentOrderRow, ValueError)
    assert not issubclass(EX.IncoherentOrderRow, (KeyboardInterrupt, SystemExit))


def test_the_incoherent_row_books_no_fill_anywhere(tmp_path, rows):
    """The whole point: nothing reaches inventory. If a future edit returns a
    dict instead of raising, this catches the zero fill being booked."""
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    mkt = "cpc-btc-updown-15m-2026-09-27-1300z"
    store.record_submit("c1", {"market": mkt, "side": "UP",
                               "qty": "0.9", "price": "0.31"})
    before = json.dumps(store.state, sort_keys=True, default=str)
    try:
        prog = EX.order_progress(rows["incoherent_as_served_at_1311z"])
        store.resolve_pending_terminal("c1", prog)
    except EX.IncoherentOrderRow:
        pass
    assert json.dumps(store.state, sort_keys=True, default=str) == before, (
        "the incoherent row moved inventory state")
    assert store.residual(mkt) == D(0)

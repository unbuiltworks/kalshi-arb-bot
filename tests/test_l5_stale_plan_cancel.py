"""A leg resting on a plan the book has left must be withdrawn, once.

The retirement gate at the decision site already detects an unexecutable
frozen plan and already re-plans the round -- but only once nothing is live.
Nothing withdrew a resting leg BECAUSE its sibling had become unexecutable,
so the round was held until `maybe_ts_deadline` fired on the WINDOW at
`L5_TS_CANCEL_MARGIN_S` before its end. On 2026-10-27 0100z that left a leg
resting from 01:02 to 01:10 while its sibling provably could not be placed,
and rounds 2 to 4 never ran.

These tests pin the narrow withdrawal and, far more importantly, every state
in which it must do NOTHING: any fill, a partial included, any unreadable
row, and any order outside this market.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as E

from tests.test_l5_e2e_window import SLUG, Book, Window

# priced at a 0.49/0.50 book, two ticks behind: UP own 0.47, DOWN own 0.48
PLAN = {
    "ok": True,
    "legs": [{"side": "UP", "action": "BUY", "price": "0.47", "qty": "2.04"},
             {"side": "DOWN", "action": "BUY", "price": "0.48", "qty": "2.04"}],
    "pair_cost": "0.9500",
    "qty_per_leg": "2.04",
}
MOVED = ("0.59", "0.60")      # DOWN's wire 0.52 is now taken by the bid


def _armed(tmp_path, *, bid="0.49", ask="0.50"):
    w = Window(tmp_path, book=Book(bid=bid, ask=ask))
    w.ex.probe_store_plan(SLUG, PLAN, 0)
    return w


def _rest(w, coid="l5f-rest", *, side="UP", qty="2.04", market=SLUG, **kw):
    """A durable live-order row, as `record_submit` writes one."""
    row = {"market": market, "side": side, "price": "0.47", "qty": qty,
           "remaining": qty, "state": "LIVE", "cum_qty": "0",
           "t": "2026-10-27T01:02:00+00:00"}
    row.update(kw)
    w.store.state.setdefault("live_orders", {})[coid] = row
    w.store.save()                       # durable, so a restart sees it
    return coid


# --------------------------------------------------------------- it fires

def test_names_the_resting_leg_when_nothing_is_filled(tmp_path):
    w = _armed(tmp_path)
    _rest(w)
    w.book.set(*MOVED)

    got = w.ex.stale_plan_cancel_candidates(SLUG)

    assert got is not None
    assert got["coids"] == ["l5f-rest"]
    assert got["plan_unexecutable"]
    assert got["retirement_blocked_by"] in ("ORDER_STILL_LIVE",
                                            "ORDER_STILL_FILLABLE")
    assert got["reason"] == "TS_STALE_PLAN_NO_FILL"


def test_names_every_resting_leg_of_this_market(tmp_path):
    w = _armed(tmp_path)
    _rest(w, "l5f-a", side="UP")
    _rest(w, "l5f-b", side="DOWN")
    w.book.set(*MOVED)

    assert w.ex.stale_plan_cancel_candidates(SLUG)["coids"] == ["l5f-a",
                                                                "l5f-b"]


# ------------------------------------------------- exposure forbids it

@pytest.mark.parametrize("qty", ["2.04", "0.01"])
def test_any_position_including_a_partial_cancels_nothing(tmp_path, qty):
    """A fill of ANY size keeps its cost basis and stays on completion."""
    w = _armed(tmp_path)
    _rest(w)
    w.book.set(*MOVED)
    w.store.state["positions"]["%s|UP" % SLUG] = qty

    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


def test_a_booked_partial_on_the_row_cancels_nothing(tmp_path):
    """Exposure can be on the row before it reaches `positions`."""
    w = _armed(tmp_path)
    _rest(w, cum_qty="0.01", remaining="2.03")
    w.book.set(*MOVED)

    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


def test_unreadable_booked_quantity_cancels_nothing(tmp_path):
    """UNREADABLE IS NOT ZERO: the row may carry a partial."""
    w = _armed(tmp_path)
    _rest(w, cum_qty=None, filled=None)
    w.book.set(*MOVED)

    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


def test_an_order_in_another_market_cancels_nothing(tmp_path):
    w = _armed(tmp_path)
    _rest(w)
    _rest(w, "l5f-elsewhere", market="cpc-btc-updown-15m-2026-10-27-0200z")
    w.book.set(*MOVED)

    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


# ------------------------------------------------ the plan must be stale

def test_an_executable_plan_cancels_nothing(tmp_path):
    """No book movement: the freeze is doing its job and is left alone."""
    w = _armed(tmp_path)
    _rest(w)

    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


def test_no_stored_plan_cancels_nothing(tmp_path):
    w = Window(tmp_path, book=Book(bid=MOVED[0], ask=MOVED[1]))
    _rest(w)

    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


def test_nothing_resting_cancels_nothing(tmp_path):
    """Already retirable: the existing gate re-plans without help."""
    w = _armed(tmp_path)
    w.book.set(*MOVED)

    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


# ---------------------------------------------------------- fails closed

def test_unreadable_book_cancels_nothing(tmp_path):
    w = _armed(tmp_path)
    _rest(w)
    w.book.disconnect()

    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


def test_a_raising_helper_cancels_nothing(tmp_path):
    w = _armed(tmp_path)
    _rest(w)
    w.book.set(*MOVED)

    def boom(*_a, **_k):
        raise RuntimeError("store exploded")

    w.ex.probe_round_is_safe_to_retire = boom
    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


def test_inventory_blocker_is_not_mistaken_for_a_live_order(tmp_path):
    w = _armed(tmp_path)
    _rest(w)
    w.book.set(*MOVED)
    w.ex.probe_round_is_safe_to_retire = (
        lambda *_a, **_k: "LEG_HOLDS_INVENTORY:UP")

    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


def test_unreadable_state_blocker_cancels_nothing(tmp_path):
    w = _armed(tmp_path)
    _rest(w)
    w.book.set(*MOVED)
    w.ex.probe_round_is_safe_to_retire = (
        lambda *_a, **_k: "STATE_UNREADABLE:RuntimeError")

    assert w.ex.stale_plan_cancel_candidates(SLUG) is None


# --------------------------------------------------- it retires nothing

def test_it_does_not_retire_or_discard_the_round_itself(tmp_path):
    """Naming candidates must not touch the round or its plan."""
    w = _armed(tmp_path)
    _rest(w)
    w.book.set(*MOVED)

    w.ex.stale_plan_cancel_candidates(SLUG)

    assert w.ex.probe_stored_plan(SLUG, 0) is not None
    assert w.ex.probe_round_retired(SLUG, 0) is False


def test_survives_a_restart_against_the_same_durable_state(tmp_path):
    w = _armed(tmp_path)
    _rest(w)
    w.book.set(*MOVED)

    fresh = w.restart(tmp_path)
    got = fresh.ex.stale_plan_cancel_candidates(SLUG)

    assert got is not None and got["coids"] == ["l5f-rest"]

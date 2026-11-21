"""The deadman must cancel the VENUE's order, and prove it did.

Every test here is written against the three defects the old four-line
deadman had: it cancelled by client order id (which cancels nothing), it
iterated the never-pruned submit log (so it would re-cancel history forever),
and it reported success on any non-raising call (so the record lied).

The contract under test: cancel by venue order id, classify by a venue
re-read and never by the ack, book any fill found on the way, and mark the
book uncertain on every outcome that is not a venue-confirmed terminal state.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as E


def st(tmp_path, name="inv.json"):
    return E.InventoryStore(str(tmp_path / name))


def order_row(oid, *, state, cum="0", leaves="1", avg="0", comm="0"):
    """A venue /v1/order body in the venue's own shape."""
    return {"id": oid, "state": state, "cumQuantity": cum,
            "leavesQuantity": leaves,
            "avgPx": {"value": avg, "currency": "USD"},
            "commissionNotionalTotalCollected": {"value": comm,
                                                 "currency": "USD"}}


class Venue:
    """Records what it was asked to cancel, and answers reads from a table."""

    market_slug = "cpc-btc-updown-15m-2026-10-12-2200z"

    def __init__(self, rows, *, cancel_raises=(), order_raises=()):
        self.rows = dict(rows)
        self.cancel_raises = set(cancel_raises)
        self.order_raises = set(order_raises)
        self.cancelled = []
        self.read = []

    def cancel(self, order_id, market_slug=None):
        self.cancelled.append(order_id)
        if order_id in self.cancel_raises:
            raise RuntimeError("venue said no")
        return {"ok": True}

    def order(self, order_id):
        self.read.append(order_id)
        if order_id in self.order_raises:
            raise TimeoutError("read timed out")
        return self.rows[order_id]


def rest(store, coid, vid, *, market=Venue.market_slug, side="UP",
         qty="1.00", price="0.60"):
    """Put one order on the book the way a real submit does."""
    store.record_submit(coid, {"market": market, "side": side,
                               "qty": D(qty), "price": D(price)})
    if vid is not None:
        store.attach_venue_id(coid, vid)
    return store


# ---------------------------------------------------------------- the id

def test_the_cancel_names_the_venue_id_and_never_the_client_id(tmp_path):
    s = rest(st(tmp_path), "coid-1", "D1VENUE001")
    v = Venue({"D1VENUE001": order_row("D1VENUE001",
                                       state="ORDER_STATE_CANCELED",
                                       leaves="0")})
    out = E.deadman(s, v, reason="window_end")
    assert v.cancelled == ["D1VENUE001"], (
        "the venue cancels by its own id; the client order id cancels nothing")
    assert "coid-1" not in v.cancelled
    assert [r["venue_order_id"] for r in out["cancelled"]] == ["D1VENUE001"]


def test_an_order_with_no_venue_id_is_uncertainty_not_a_no_op(tmp_path):
    s = rest(st(tmp_path), "coid-1", None)
    v = Venue({})
    out = E.deadman(s, v, reason="shutdown")
    assert v.cancelled == [] and v.read == []
    assert out["no_venue_id"] == [
        {"client_order_id": "coid-1", "market": Venue.market_slug,
         "side": "UP"}]
    assert out["cancelled"] == []
    assert s.orders_uncertain(), (
        "an order we cannot name may be resting; that is not all-clear")


def test_the_permanent_submit_log_is_not_the_cancel_list(tmp_path):
    """History must not be re-cancelled at every window end."""
    s = st(tmp_path)
    rest(s, "old", "D1OLD0001")
    s.terminate_order("old", reason="ORDER_STATE_FILLED")   # retired
    rest(s, "new", "D1NEW0001")
    assert "old" in (s.state.get("submitted") or {}), "the log keeps history"
    v = Venue({"D1NEW0001": order_row("D1NEW0001",
                                      state="ORDER_STATE_CANCELED",
                                      leaves="0")})
    E.deadman(s, v, reason="window_end")
    assert v.cancelled == ["D1NEW0001"]


def test_nothing_resting_sends_nothing(tmp_path):
    v = Venue({})
    out = E.deadman(st(tmp_path), v, reason="shutdown")
    assert v.cancelled == [] and v.read == []
    assert out["cancelled"] == [] and out["still_open"] == []


# ------------------------------------------------------- the ack is not proof

def test_a_two_hundred_ack_with_the_order_still_live_is_not_a_cancellation(
        tmp_path):
    s = rest(st(tmp_path), "coid-1", "D1STILL001")
    v = Venue({"D1STILL001": order_row("D1STILL001",
                                       state="ORDER_STATE_OPEN",
                                       leaves="1.00")})
    out = E.deadman(s, v, reason="window_end")
    assert out["cancelled"] == []
    assert [r["venue_order_id"] for r in out["still_open"]] == ["D1STILL001"]
    assert s.orders_uncertain(), "an unmanaged resting order must halt us"
    assert "still reports it live" in s.orders_uncertain()


def test_an_ambiguous_ack_is_resolved_by_the_reread_not_retried(tmp_path):
    s = rest(st(tmp_path), "coid-1", "D1AMBIG001")
    v = Venue({"D1AMBIG001": order_row("D1AMBIG001",
                                       state="ORDER_STATE_CANCELED",
                                       leaves="0")},
              cancel_raises=["D1AMBIG001"])
    out = E.deadman(s, v, reason="shutdown")
    assert v.cancelled == ["D1AMBIG001"], "never retried"
    assert len(out["cancelled"]) == 1, (
        "the venue says terminal, so it is cancelled whatever the ack did")
    assert out["errors"][0]["resolved_by_reread"] is True
    assert not s.orders_uncertain()


def test_an_unreadable_order_is_unverified_and_never_cancelled(tmp_path):
    s = rest(st(tmp_path), "coid-1", "D1DARK001")
    v = Venue({}, order_raises=["D1DARK001"])
    out = E.deadman(s, v, reason="shutdown")
    assert out["cancelled"] == [] and out["still_open"] == []
    assert out["unverified"][0]["verify_error"] == "TimeoutError"
    assert s.orders_uncertain()


# ------------------------------------------------------------------ fills

def test_a_partial_filled_before_the_cancel_is_booked_with_its_cost(tmp_path):
    s = rest(st(tmp_path), "coid-1", "D1PART001", qty="1.00", price="0.60")
    v = Venue({"D1PART001": order_row(
        "D1PART001", state="ORDER_STATE_PARTIALLY_FILLED", cum="0.40",
        leaves="0.60", avg="0.60", comm="0.0000")})
    out = E.deadman(s, v, reason="window_end")
    assert out["filled"][0]["qty"] == "0.40"
    assert s.position(Venue.market_slug, "UP") == D("0.40"), (
        "shares that arrived must carry a cost basis, not appear from nowhere")
    assert s.leg_cost(Venue.market_slug).get("UP", D(0)) > 0
    # still working, so it is NOT a cancellation
    assert out["still_open"] and s.orders_uncertain()


def test_a_full_fill_discovered_on_cancel_is_terminal_and_booked(tmp_path):
    s = rest(st(tmp_path), "coid-1", "D1FULL001", qty="1.00", price="0.60")
    v = Venue({"D1FULL001": order_row(
        "D1FULL001", state="ORDER_STATE_FILLED", cum="1.00", leaves="0",
        avg="0.60")})
    out = E.deadman(s, v, reason="window_end")
    assert out["filled"][0]["qty"] == "1.00"
    assert len(out["cancelled"]) == 1
    assert s.position(Venue.market_slug, "UP") == D("1.00")
    assert not s.orders_uncertain()
    assert s.live_order_count() == 0, "a terminal order frees the slot"


def test_a_fill_is_booked_once_however_often_the_deadman_runs(tmp_path):
    s = rest(st(tmp_path), "coid-1", "D1ONCE001", qty="1.00")
    v = Venue({"D1ONCE001": order_row(
        "D1ONCE001", state="ORDER_STATE_FILLED", cum="1.00", leaves="0",
        avg="0.60")})
    E.deadman(s, v, reason="window_end")
    E.deadman(s, v, reason="shutdown")
    assert s.position(Venue.market_slug, "UP") == D("1.00")


# --------------------------------------------------------- expiry, restart

def test_an_expired_order_reads_as_cancelled_without_a_fill(tmp_path):
    s = rest(st(tmp_path), "coid-1", "D1EXP0001")
    v = Venue({"D1EXP0001": order_row("D1EXP0001",
                                      state="ORDER_STATE_EXPIRED",
                                      leaves="0")})
    out = E.deadman(s, v, reason="market_expired")
    assert len(out["cancelled"]) == 1 and out["filled"] == []
    assert not s.orders_uncertain()


def test_a_restarted_process_still_cancels_what_the_ledger_remembers(
        tmp_path):
    """The ledger outlives the process; so must the cancel path."""
    s = rest(st(tmp_path), "coid-1", "D1BOOT001")
    reborn = E.InventoryStore(str(tmp_path / "inv.json"))
    assert "coid-1" in reborn.live_orders()
    v = Venue({"D1BOOT001": order_row("D1BOOT001",
                                      state="ORDER_STATE_CANCELED",
                                      leaves="0")})
    out = E.deadman(reborn, v, reason="startup_deadman")
    assert [r["venue_order_id"] for r in out["cancelled"]] == ["D1BOOT001"]


def test_every_resting_order_is_attempted_even_when_one_fails(tmp_path):
    s = st(tmp_path)
    rest(s, "a", "D1A00001")
    rest(s, "b", "D1B00001")
    v = Venue({"D1A00001": order_row("D1A00001",
                                     state="ORDER_STATE_CANCELED",
                                     leaves="0"),
               "D1B00001": order_row("D1B00001",
                                     state="ORDER_STATE_CANCELED",
                                     leaves="0")},
              order_raises=["D1A00001"])
    out = E.deadman(s, v, reason="shutdown")
    assert set(v.cancelled) == {"D1A00001", "D1B00001"}
    assert len(out["unverified"]) == 1 and len(out["cancelled"]) == 1
    assert s.orders_uncertain(), "one unknown fate is enough to halt"


def test_uncertainty_from_the_deadman_refuses_the_next_submission(tmp_path):
    """The halt is not advisory: it is the first check in refuse()."""
    s = rest(st(tmp_path), "coid-1", "D1STILL002")
    v = Venue({"D1STILL002": order_row("D1STILL002",
                                       state="ORDER_STATE_OPEN",
                                       leaves="1.00")})
    E.deadman(s, v, reason="window_end")
    lim = E.SafetyLimits()
    why = lim.refuse(price=D("0.60"), qty=D("1"), market=Venue.market_slug,
                     committed_by_market={}, residual_after=D(0),
                     realized_today=D(0), open_markets=[],
                     outstanding_by_market={}, live_order_count=0,
                     orders_uncertain=s.orders_uncertain())
    assert why == "ORDER_STATE_UNCERTAIN"


def test_the_record_never_calls_an_unconfirmed_cancel_a_cancellation(
        tmp_path):
    """No outcome but a venue-confirmed terminal state lands in `cancelled`."""
    s = st(tmp_path)
    rest(s, "live", "D1L00001")
    rest(s, "dark", "D1D00001")
    rest(s, "noid", None)
    v = Venue({"D1L00001": order_row("D1L00001", state="ORDER_STATE_OPEN",
                                     leaves="1.00")},
              order_raises=["D1D00001"])
    out = E.deadman(s, v, reason="shutdown")
    assert out["cancelled"] == []
    assert len(out["still_open"]) == 1
    assert len(out["unverified"]) == 1
    assert len(out["no_venue_id"]) == 1

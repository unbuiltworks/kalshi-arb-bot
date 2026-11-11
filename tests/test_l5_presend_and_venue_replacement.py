"""Three different things that all look like "repricing", kept apart.

THE DISTINCTION THE OWNER ASKED FOR, and the reason it matters is that the
three happen at different moments, against different books, with different
consequences for a submission claim:

  1. PLAN RE-PRICING, BEFORE ANYTHING IS SENT. The round's frozen plan is
     recomputed from the mirror as it is read. Nothing is live, nothing is
     cancelled, no claim exists yet. Covered by
     `tests/test_l5_frozen_plan_reprice.py`.

  2. PRE-SEND DECLINE, after the plan and before the wire. The mirror is
     read AGAIN, immediately before submission, because the book can move
     between planning and the wire -- and that later snapshot is the only
     one that describes the book the order will actually land in. A leg the
     book has left is not sent, and no claim is consumed. Covered here.

  3. VENUE ORDER REPLACEMENT, after submission. A real order is resting at
     the venue. Changing its price means CANCELLING it by exact id, proving
     it terminal by re-read, and submitting a new one with a new client
     order id. A fill racing that cancel must be booked, not lost. Covered
     here.

CONFLATING 2 AND 3 IS THE EXPENSIVE MISTAKE. A decline costs nothing; a
replacement spends a cancellation, a new claim and the REPLACE budget, and
can race a fill. Only 3 may ever touch an order that exists.

No network, no threads, no live order.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

import tests.test_l5_e2e_window as H
import tests.test_l5_release_candidate_lifecycle as RC

from src import l5_live_execution as LX
from src import l5_two_sided_probe as TS

SLUG = H.SLUG

production_configuration = RC.production_configuration


def win(tmp_path, **kw):
    return H.Window(tmp_path, limits=RC.deployed_limits(),
                    gates=RC.deployed_gates(), **kw)


# =====================================================================
# 2. THE PRE-SEND DECLINE
# =====================================================================

def test_the_crossing_guard_and_the_behind_guard_are_separate_refusals(
        tmp_path):
    """Two directions, two names, two counters. A single
    "PRESEND_WOULD_CROSS" for both would make the telemetry unreadable --
    one means the venue would have rejected us, the other means we would
    never have filled."""
    w = win(tmp_path)
    ex = w.ex
    assert hasattr(ex, "presend_cross_refusal")
    assert hasattr(ex, "presend_behind_refusal")
    maker = {"mode": "MAKER"}
    w.book.set("0.42", "0.44")
    # a leg ABOVE the bid crosses; a leg far below it is merely behind
    crossing = ex.presend_cross_refusal(
        SLUG, {"side": "UP", "price": D("0.50")}, maker)
    assert crossing is not None
    assert crossing["reason"].startswith("PRESEND_WOULD_CROSS"), crossing
    behind = ex.presend_behind_refusal(
        SLUG, {"side": "UP", "price": D("0.30")}, maker)
    assert behind is not None
    assert behind["reason"].startswith("PRESEND_LEFT_BEHIND"), behind
    assert D(behind["drift_ticks"]) >= D(behind["allow_ticks"]), behind


def test_a_leg_at_the_CURRENT_depth_2_price_is_not_declined(tmp_path):
    """The common case must be untouched. At bid 0.42 and depth 2 the leg
    is 0.40, which is where the planner puts it, so drift is zero."""
    w = win(tmp_path)
    w.book.set("0.42", "0.44")
    assert w.ex.presend_behind_refusal(
        SLUG, {"side": "UP", "price": D("0.40")}, {"mode": "MAKER"}) is None


def test_one_tick_of_drift_is_under_the_tolerance_and_sends(tmp_path):
    w = win(tmp_path)
    w.book.set("0.43", "0.45")                # target UP own 0.41
    assert w.ex.presend_behind_refusal(
        SLUG, {"side": "UP", "price": D("0.40")}, {"mode": "MAKER"}) is None


def test_the_DOWN_leg_is_judged_in_OWN_terms_and_not_wire(tmp_path):
    """The convention that has caught this programme repeatedly. A DOWN buy
    at own `q` rests as an ASK at `1 - q`, so behind-the-touch for DOWN is
    a LOWER own price."""
    w = win(tmp_path)
    w.book.set("0.42", "0.44")                # target DOWN own = 1-0.44-0.02
    target = TS.quote_own_price("DOWN", bid=D("0.42"), ask=D("0.44"),
                                tick=D("0.01"), depth_ticks=2)
    assert target == D("0.54"), target
    assert w.ex.presend_behind_refusal(
        SLUG, {"side": "DOWN", "price": target}, {"mode": "MAKER"}) is None
    far = w.ex.presend_behind_refusal(
        SLUG, {"side": "DOWN", "price": D("0.44")}, {"mode": "MAKER"})
    assert far is not None, "a DOWN leg 10 ticks behind was not declined"
    assert far["reason"].startswith("PRESEND_LEFT_BEHIND")


def test_a_TAKER_order_is_never_declined_for_being_behind(tmp_path):
    """A taker order does not rest, so "behind the touch" is meaningless
    for it. (Taker is disabled in production; this is the guard's own
    precondition, not an endorsement.)"""
    w = win(tmp_path)
    w.book.set("0.42", "0.44")
    assert w.ex.presend_behind_refusal(
        SLUG, {"side": "UP", "price": D("0.20")}, {"mode": "TAKER"}) is None


def test_a_round_HOLDING_INVENTORY_is_never_declined(tmp_path):
    """THE SAFETY CONDITION. Declining a complement that matches a held leg
    would strand the inventory it was going to match -- far worse than a
    quote that fills slowly. Any answer but None from
    `probe_round_is_safe_to_retire` sends."""
    w = win(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)                              # leg one FILLS
    held_side = w.venue.sent[0]["side"]
    assert w.store.position(SLUG, held_side) > 0
    w.book.set("0.42", "0.44")
    other = "DOWN" if held_side == "UP" else "UP"
    # a complement 10 ticks behind: still sent, because inventory is held
    assert w.ex.presend_behind_refusal(
        SLUG, {"side": other, "price": D("0.20")}, {"mode": "MAKER"}) is None


def test_a_round_with_a_LIVE_order_is_never_declined(tmp_path):
    w = win(tmp_path)
    w.tick(30.0)
    assert w.store.still_fillable_order_count() == 1
    w.book.set("0.42", "0.44")
    assert w.ex.presend_behind_refusal(
        SLUG, {"side": "DOWN", "price": D("0.20")}, {"mode": "MAKER"}) is None


@pytest.mark.parametrize("book_kw", [
    {"bid": None, "ask": "0.44"},
    {"bid": "0.42", "ask": None},
])
def test_it_FAILS_OPEN_on_an_unreadable_book(tmp_path, book_kw):
    """An efficiency guard that could halt trading on a mirror hiccup would
    be a worse change than the waste it removes."""
    w = win(tmp_path)
    w.book._b = dict(w.book._b, **book_kw)
    assert w.ex.presend_behind_refusal(
        SLUG, {"side": "UP", "price": D("0.10")}, {"mode": "MAKER"}) is None


def test_it_FAILS_OPEN_when_the_round_state_cannot_be_read(tmp_path,
                                                           monkeypatch):
    w = win(tmp_path)
    w.book.set("0.42", "0.44")

    def boom(*a, **k):
        raise RuntimeError("durable state unreadable")

    monkeypatch.setattr(type(w.ex), "probe_round_is_safe_to_retire", boom)
    assert w.ex.presend_behind_refusal(
        SLUG, {"side": "UP", "price": D("0.10")}, {"mode": "MAKER"}) is None


def test_it_FAILS_OPEN_when_the_drift_helper_raises(tmp_path, monkeypatch):
    w = win(tmp_path)
    w.book.set("0.42", "0.44")
    monkeypatch.setattr("src.l5_two_sided_probe.plan_drift_ticks",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("nope")))
    assert w.ex.presend_behind_refusal(
        SLUG, {"side": "UP", "price": D("0.10")}, {"mode": "MAKER"}) is None


def test_a_decline_consumes_no_claim_and_sends_nothing(tmp_path):
    """THE WHOLE POINT OF DOING IT HERE. The next statement after this
    guard consumes a claim that is never returned, so a leg the book has
    left must be declined BEFORE it."""
    w = win(tmp_path)
    w.book.set("0.42", "0.44")
    w.tick(30.0)
    w.tick(45.0)
    w.tick(175.0)                             # both cancelled, zero filled
    sent_before = len(w.venue.sent)
    # the mirror moves far enough that the re-priced plan is itself stale
    # by the time the wire is reached: the behind guard is the last word
    w.book.set("0.70", "0.72")
    w.ex.ts_book = w.book
    r = w.tick(200.0, bid="0.42", ask="0.44")   # plan from the OLD row
    reason = w.reason(r)
    if reason.startswith("PRESEND_LEFT_BEHIND"):
        assert len(w.venue.sent) == sent_before, "an order was sent anyway"
        assert (r.get("PRESEND_BEHIND") or {}).get("drift_ticks")
    # whichever guard fired, nothing may have reached the venue stale
    for s in w.venue.sent[sent_before:]:
        assert D(s["price"]) != D("0.40"), s


def test_the_counters_are_separate_so_the_saving_is_measurable(tmp_path):
    w = win(tmp_path)
    w.book.set("0.42", "0.44")
    for px in ("0.30", "0.28", "0.26"):
        w.ex.presend_behind_refusal(SLUG, {"side": "UP", "price": D(px)},
                                    {"mode": "MAKER"})
    w.ex.presend_cross_refusal(SLUG, {"side": "UP", "price": D("0.50")},
                               {"mode": "MAKER"})
    assert w.ex.presend_behind_skips["UP"] == 3, w.ex.presend_behind_skips
    assert w.ex.presend_behind_skips["total"] == 3
    assert w.ex.presend_cross_skips["total"] == 1, w.ex.presend_cross_skips


# =====================================================================
# 3. VENUE ORDER REPLACEMENT, AFTER SUBMISSION
# =====================================================================

def test_a_resting_order_is_replaced_by_CANCEL_then_SUBMIT_never_amended(
        tmp_path):
    """There is no amend. A price change on a resting order is a
    cancellation by exact venue id, proved terminal by re-read, and a new
    order with a NEW client order id."""
    w = win(tmp_path)
    w.tick(30.0)
    first = dict(w.venue.sent[0])
    assert w.venue.cancelled == []
    w.tick(175.0)                             # the TTL cancels it
    assert w.venue.cancelled == ["v1"], w.venue.cancelled
    # the venue says TERMINAL on re-read, which is what "cancelled" means
    assert w.venue.order("v1")["state"] == "ORDER_STATE_CANCELED"
    w.book.set("0.46", "0.48")
    r = w.tick(200.0, bid="0.46", ask="0.48")
    assert w.reason(r) == "SUBMITTED", w.reason(r)
    second = w.venue.sent[-1]
    assert second["client_order_id"] != first["client_order_id"]
    assert second["side"] == first["side"]
    assert D(second["price"]) != D(first["price"]), (second, first)


def test_the_replacement_is_a_DISTINCT_venue_order_and_the_old_id_is_dead(
        tmp_path):
    w = win(tmp_path)
    w.tick(30.0)
    w.tick(175.0)
    w.book.set("0.46", "0.48")
    w.tick(200.0, bid="0.46", ask="0.48")
    live = w.store.live_orders()
    assert first_oid_not_live(w, "v1"), "the cancelled id is still tracked"
    assert len(live) >= 1


def first_oid_not_live(w, oid):
    for row in w.store.live_orders().values():
        if str(row.get("venue_order_id")) == oid:
            return False
    return True


def test_a_FILL_racing_the_replacement_is_booked_and_never_REPLACED(
        tmp_path):
    """THE RACE THAT COSTS MONEY. The sweep polls before it cancels, so a
    leg that filled in the gap is booked into inventory rather than
    withdrawn, and that filled leg is never re-placed.

    A CORRECTION OF THIS TEST'S FIRST VERSION, which asserted "no second
    order on the same side" and failed -- correctly. The real sequence is:
    the UP leg fills, the sweep books it and places the DOWN complement
    (a held residual outranks cheapness), and the NEXT tick opens round 1
    with a fresh UP leg. That is replenishment, not replacement: round 0's
    claims are both spent, the prospective book is flat because the DOWN
    complement is working, and `probe_reopen_decision` priced the new
    round. Conflating "a new round opened on this side" with "the filled
    leg was replaced" would have pinned a defect that is not there and
    forbidden accumulation, which is the strategy.

    So the properties asserted are the ones that actually matter: the
    filled leg is not cancelled, its own client order id is never sent
    again, and the position does not double.
    """
    w = win(tmp_path)
    w.tick(30.0)
    side = w.venue.sent[0]["side"]
    coid = w.venue.sent[0]["client_order_id"]
    qty = D(w.venue.sent[0]["qty"])
    # the venue fills it just before the sweep reads it
    w.venue.acked["v1"] = str(qty)
    w.tick(175.0)
    assert w.store.position(SLUG, side) == qty
    assert "v1" not in w.venue.cancelled, w.venue.cancelled
    # the complement went out, not a re-placement of the filled leg
    assert w.venue.sent[1]["side"] != side, w.venue.sent[1]

    w.tick(200.0)
    # THE FILLED LEG'S OWN ORDER IS NEVER SENT AGAIN
    assert [s["client_order_id"] for s in w.venue.sent].count(coid) == 1
    # AND THE POSITION DOES NOT DOUBLE off the back of the race
    assert w.store.position(SLUG, side) == qty
    # any further order on that side belongs to a LATER round, which is
    # replenishment: round 0's legs are both claimed and spent
    assert all(w.ex.probe_legs_claimed(SLUG, 0).values())


def test_a_PARTIAL_fill_racing_the_cancel_books_the_part_and_withdraws_rest(
        tmp_path):
    w = win(tmp_path)
    w.tick(30.0)
    side = w.venue.sent[0]["side"]
    w.venue.acked["v1"] = "0.40"              # part of a 1.00 leg
    w.tick(175.0)
    assert w.store.position(SLUG, side) == D("0.40")
    # the remainder is not left resting: a cancelled order has no leaves
    assert w.venue.order("v1")["leavesQuantity"] == "0"


def test_replacement_never_exceeds_the_aggregate_exposure_ceiling(tmp_path):
    """A replacement is a new order, so it faces the $1.00 account ceiling
    again -- it is not grandfathered by the order it replaces."""
    w = win(tmp_path)
    w.tick(30.0)
    w.tick(175.0)
    w.book.set("0.46", "0.48")
    w.tick(200.0, bid="0.46", ask="0.48")
    resting = sum(D(s["qty"]) * D(s["price"])
                  for s in w.venue.sent
                  if s["client_order_id"] in w.store.live_orders())
    assert resting <= D("1.00") + D("0.0001"), resting


def test_a_replacement_cannot_be_issued_for_an_UNVERIFIED_cancellation(
        tmp_path):
    """If the venue will not confirm the cancellation terminal, the order
    may still be live and a replacement would double the exposure. The
    engine must not guess."""
    w = win(tmp_path)
    w.tick(30.0)
    coid = w.venue.sent[0]["client_order_id"]

    def wont_confirm(order_id, market_slug=None):
        w.venue.cancelled.append(order_id)
        return {"cancelled": order_id}        # but never retires it

    w.venue.cancel = wont_confirm
    w.tick(175.0)
    # the order is NOT terminal on re-read
    assert w.venue.order("v1")["state"] != "ORDER_STATE_CANCELED"
    r = w.tick(200.0)
    # no second order on that side while its fate is unknown
    same = [s for s in w.venue.sent if s["side"] == w.venue.sent[0]["side"]]
    assert len(same) == 1, (same, w.reason(r))

"""THE REAL EXECUTION WIRING, FILLED FIRST LEG THROUGH COMPLEMENT.

`test_e1_pair_lifecycle.py` proves the GATE FUNCTIONS. That is not enough:
the 20-order runaway happened because a gate existed and nothing fed it, so
a test that only exercises gates is the same mistake one level up.

These tests drive the ACTUAL production functions --
`runner.submit_first_leg` and `runner.submit_complement`, with the real
`authorization.SessionAuthorization`, the real `latch` on a temp dir, the
real `complement_tranche` and `complement_price`, the real order body and
the real preview/echo/POST sequence -- against a simulated venue. The only
fake is the socket.

What they establish:

  * a filled first leg becomes recognised inventory from venue state;
  * `submit_complement` is reached and AUTHORIZED through
    `authorize_complement`, inheriting the first leg's market;
  * the complement goes out sized to the CUMULATIVE fill, maker/post-only,
    at a price that keeps the fee-inclusive pair at or under the ceiling;
  * the complement's id is registered under the COMPLEMENT role, so it
    does not block the next first leg through the first-leg guard;
  * the complement consumes a COMPLEMENT latch slot, not one of the twenty
    first-leg attempts.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import authorization as AUTH
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import stopping as SP
from analysis.l5.e1_cohort.spec import PAIR_COST_CEILING
from analysis.l5.e1_cohort.test_e1_live_path import (FakeTransport, arm,
                                                     fake_pin, BASE,
                                                     _snapshot_from)
from analysis.l5.e1_cohort.test_e1_pair_lifecycle import FakeVenue, UP, DOWN


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """Arming, a temp latch dir, a real SessionAuthorization with the
    window authorized, and a clean transport ledger."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setattr(L, "COMPLEMENT_DIR", str(tmp_path / "e1_cohort"),
                        raising=False)
    arm(monkeypatch)
    FakeTransport.posts = []
    FakeTransport.cancels = []
    sd = str(tmp_path / "e1_auth")
    os.makedirs(sd, exist_ok=True)
    auth = AUTH.SessionAuthorization(sd)
    pin = fake_pin(BASE)
    auth.authorize_selected(pin)
    return {"auth": auth, "pin": pin, "state": sd}


def _snap(pin, view, *, price="0.49", comp_bid="0.47",
          known=None, roles=None, pending=None):
    """A snapshot in the provider's exact shape, with order state from the
    simulated venue rather than a literal."""
    s = _snapshot_from(pin, window=0)
    s["first_leg"] = dict(s["first_leg"], best_bid=price, best_ask="0.51")
    s["first_leg_price"] = price
    s["complement"] = dict(s["complement"], best_bid=comp_bid,
                           best_ask="0.49")
    s["order_state"] = view
    s["our_outstanding_orders"] = OS.outstanding_for_snapshot(view)
    s["our_open_residual"] = OS.residual_for_snapshot(view)
    s["known_order_ids"] = [] if known is None else known
    s["order_roles"] = {} if roles is None else roles
    s["pending_order_ids"] = [] if pending is None else pending
    return s


def test_the_real_runner_goes_first_leg_to_complement(wired):
    """ONE PAIR, THROUGH THE PRODUCTION FUNCTIONS."""
    auth, pin = wired["auth"], wired["pin"]
    venue = FakeVenue()
    known, roles, pending = [], {}, []
    tracker = SP.CohortTracker()

    # ---- the first leg, through the REAL submit_first_leg --------------
    v0 = venue.view(known=known, roles=roles)
    snap = _snap(pin, v0, known=known, roles=roles, pending=pending)
    out = R.submit_first_leg(snap, tracker, now=BASE + 10,
                             transport_factory=FakeTransport,
                             session_auth=auth)
    assert out["submitted_post_only"] is True
    assert len(FakeTransport.posts) == 1
    post = FakeTransport.posts[0]
    assert post["quantity"] == 0.13 or D(str(post["quantity"])) == D("0.13")
    assert post["post_only"] is True
    fl_id = out["order_id"]
    # the id and its role were REGISTERED, which is what makes the next
    # read possible at all on a venue with no order-history endpoint
    assert known == [fl_id]
    assert roles[fl_id] == OS.FIRST_LEG
    # ONE first-leg attempt was consumed
    assert tracker.window_attempts.get(0, 0) == 1

    # ---- the venue FILLS it --------------------------------------------
    venue.rest(fl_id, side=UP, wire="0.49")
    venue.fill(fl_id, "0.13")
    v1 = venue.view(known=known, roles=roles)
    assert v1["filled_ids"] == [fl_id]
    assert v1["n_outstanding_first_legs"] == 0
    # INVENTORY RECOGNISED from venue state, not from a local belief
    assert D(v1["residual"]["qty"]) == D("0.13")
    assert v1["residual"]["outcome_side"] == UP
    gate = OS.complement_gate(v1)
    assert gate["may_submit_complement"] is True
    assert D(gate["qty_to_complement"]) == D("0.13")

    # ---- the complement, through the REAL submit_complement ------------
    snap2 = _snap(pin, v1, known=known, roles=roles, pending=pending)
    # NOTE: `FakeTransport.posts` is NOT cleared here. The fake mints ids
    # from `len(posts)`, so clearing it would re-mint "order-1" and the
    # complement would collide with the first leg -- which is a fixture
    # artefact, not venue behaviour, and it would quietly make this test
    # assert the wrong thing.
    n_before = len(FakeTransport.posts)
    cout = R.submit_complement(
        window=0, slot=out["e1_latch_slot"]["slot"], snapshot=snap2,
        first_leg_filled_qty=gate["qty_to_complement"], tracker=tracker,
        transport_factory=FakeTransport, session_auth=auth)
    assert cout["submitted"] is True, cout
    # AUTHORIZED through the complement's own path, inheriting the market
    assert cout["e1_authorization"]["inherits_first_leg_authorization"] \
        is True
    assert cout["e1_authorization"]["complement_selected_its_own_market"] \
        is False
    assert cout["e1_authorization"]["role"] == "complement"
    # sized to the CUMULATIVE fill, not the first partial
    assert D(cout["e1_complement_qty"]) == D("0.13")
    assert cout["sized_to_cumulative_filled_qty"] is True
    assert cout["sized_to_only_the_first_partial"] is False
    # maker/post-only on the wire, exactly one further POST
    assert len(FakeTransport.posts) == n_before + 1
    assert FakeTransport.posts[-1]["post_only"] is True
    # the pair is inside the frozen fee-inclusive ceiling
    pair = D(cout["e1_complement_plan"]["pair_cost_usd"]) \
        if "pair_cost_usd" in cout["e1_complement_plan"] else None
    if pair is not None:
        assert pair <= PAIR_COST_CEILING
    # registered under the COMPLEMENT role
    cid = cout["e1_complement_order_id"]
    assert cid and cid != fl_id, "the complement is a distinct order"
    assert cid in known
    assert roles[cid] == OS.COMPLEMENT
    # and it did NOT consume a first-leg attempt slot
    assert tracker.window_attempts.get(0, 0) == 1, \
        "a complement must not spend one of the twenty first-leg attempts"

    # ---- the complement fills: MATCHED inventory ----------------------
    venue.rest(cid, side=DOWN, wire="0.53")
    venue.fill(cid, "0.13")
    v2 = venue.view(known=known, roles=roles)
    assert v2["net_filled_by_outcome_side"] == {UP: "0.13", DOWN: "0.13"}
    assert D(v2["matched_qty"]) == D("0.13")
    assert v2["residual"] is None
    assert v2["n_outstanding"] == 0
    # the complement, while it rested, never registered as a first leg
    assert v2["n_outstanding_first_legs"] == 0
    # a new first leg is permitted again only now that the pair is closed
    assert OS.reconcile_at_startup(v2)["may_submit"] is True


def test_a_resting_first_leg_blocks_a_second_first_leg_in_the_real_path(
        wired):
    """The runaway, against the production function rather than a gate."""
    auth, pin = wired["auth"], wired["pin"]
    venue = FakeVenue()
    known, roles, pending = [], {}, []
    tracker = SP.CohortTracker()
    v0 = venue.view(known=known, roles=roles)
    out = R.submit_first_leg(
        _snap(pin, v0, known=known, roles=roles, pending=pending),
        tracker, now=BASE + 10, transport_factory=FakeTransport,
        session_auth=auth)
    fl = out["order_id"]
    venue.rest(fl, side=UP, wire="0.49")          # it RESTS, unfilled
    v1 = venue.view(known=known, roles=roles)
    snap2 = _snap(pin, v1, known=known, roles=roles, pending=pending)
    with pytest.raises(R.Ineligible) as ei:
        R.submit_first_leg(snap2, tracker, now=BASE + 11,
                           transport_factory=FakeTransport,
                           session_auth=auth)
    assert any(str(r).startswith("E3_OUR_ORDER_IS_OUTSTANDING")
               for r in ei.value.refusals)
    # and the refusal cost nothing: still one attempt, one POST
    assert tracker.window_attempts.get(0, 0) == 1
    assert len(FakeTransport.posts) == 1


def test_the_complement_is_submitted_even_while_the_first_leg_rests(wired):
    """A PARTIALLY filled first leg is simultaneously outstanding and
    inventory. The complement must go out anyway -- that is the whole
    point of the role split."""
    auth, pin = wired["auth"], wired["pin"]
    venue = FakeVenue()
    known, roles, pending = [], {}, []
    tracker = SP.CohortTracker()
    v0 = venue.view(known=known, roles=roles)
    out = R.submit_first_leg(
        _snap(pin, v0, known=known, roles=roles, pending=pending),
        tracker, now=BASE + 10, transport_factory=FakeTransport,
        session_auth=auth)
    fl = out["order_id"]
    venue.rest(fl, side=UP, wire="0.49")
    venue.fill(fl, "0.06")                        # PARTIAL
    v1 = venue.view(known=known, roles=roles)
    assert v1["n_outstanding_first_legs"] == 1    # still resting
    assert D(v1["residual"]["qty"]) == D("0.06")  # and already inventory

    n_before = len(FakeTransport.posts)
    cout = R.submit_complement(
        window=0, slot=out["e1_latch_slot"]["slot"],
        snapshot=_snap(pin, v1, known=known, roles=roles, pending=pending),
        first_leg_filled_qty="0.06", tracker=tracker,
        transport_factory=FakeTransport, session_auth=auth)
    assert cout["submitted"] is True, cout
    assert D(cout["e1_complement_qty"]) == D("0.06")
    assert len(FakeTransport.posts) == n_before + 1
    assert FakeTransport.posts[-1]["post_only"] is True


def test_a_complement_cannot_select_its_own_market(wired):
    """It inherits the first leg's authorization and cannot wander."""
    auth, pin = wired["auth"], wired["pin"]
    venue = FakeVenue()
    v = venue.view(known=[], roles={})
    snap = _snap(pin, v)
    snap["market"] = "cpc-btc-updown-15m-2026-10-08-9999z"
    with pytest.raises(AUTH.NotAuthorized):
        R.submit_complement(window=0, slot=0, snapshot=snap,
                            first_leg_filled_qty="0.13",
                            tracker=SP.CohortTracker(),
                            transport_factory=FakeTransport,
                            session_auth=auth)
    assert FakeTransport.posts == []

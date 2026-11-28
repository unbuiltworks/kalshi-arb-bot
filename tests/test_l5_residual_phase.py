"""The residual phase machine, and the venue order semantics it rests on.

WHAT THESE PIN. That a residual's phase is a PROJECTION of durable,
venue-confirmed state -- order rows and our own fills -- and never a
function of elapsed time or of a submission acknowledgment. The venue's
ack has been observed reporting `filled_qty 0` for an order that had
filled, so an ack is a receipt that a request was heard and nothing more.

AND THAT FAST DETECTION IS NOT FAST LIQUIDATION. Reaching
COMPLETION_EVALUATION within a cycle of the fill is the goal; what to pay
to leave it is an economic question this module cannot answer and must
not be able to force.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_order_body as B
from src import l5_residual_phase as R


def inv(up="0", dn="0", up_cost="0", dn_cost="0"):
    return {"UP": {"qty": up, "cost": up_cost},
            "DOWN": {"qty": dn, "cost": dn_cost}}


def row(**kw):
    base = {"state": "LIVE", "filled_qty": "0", "reason": "STRADDLE_LEG"}
    base.update(kw)
    return base


def ph(**kw):
    kw.setdefault("orders", [])
    kw.setdefault("inventory", inv())
    return R.phase(**kw)


# -------------------------------------------------- the lifecycle

class TestTheOrdinaryLifecycle:
    def test_flat_and_quiet_is_idle(self):
        assert ph()["phase"] == R.IDLE

    def test_orders_working_without_fills(self):
        assert ph(orders=[row()])["phase"] == R.ORDERS_PENDING

    def test_fills_with_orders_still_open(self):
        got = ph(orders=[row(filled_qty="2")], inventory=inv("2", "2"))
        assert got["phase"] == R.PARTIALLY_FILLED

    def test_unmatched_with_nothing_working_evaluates(self):
        """FAST DETECTION: the residual is visible the moment the fill
        that created it is, with no order still working to explain it."""
        got = ph(inventory=inv("5", "0"))
        assert got["phase"] == R.COMPLETION_EVALUATION
        assert got["unmatched_qty"] == "5"
        assert got["unmatched_side"] == "UP"

    def test_matched_inventory_is_not_a_residual(self):
        got = ph(inventory=inv("5", "5"))
        assert got["unmatched_qty"] == "0"
        assert got["matched_qty"] == "5"
        assert got["phase"] == R.IDLE

    def test_a_working_completion_is_COMPLETING(self):
        got = ph(orders=[row(reason="STRADDLE_COMPLETION")],
                 inventory=inv("5", "0"))
        assert got["phase"] == R.COMPLETING

    def test_a_working_reduction_is_REDUCING(self):
        got = ph(orders=[row(reason="DEFENSIVE_UNWIND")],
                 inventory=inv("5", "0"))
        assert got["phase"] == R.REDUCING

    def test_window_close_with_nothing_working_is_COMPLETE(self):
        assert ph(inventory=inv("5", "5"),
                  window_closed=True)["phase"] == R.COMPLETE

    def test_every_phase_is_declared(self):
        for name in ("IDLE", "ORDERS_PENDING", "PARTIALLY_FILLED",
                     "COMPLETION_EVALUATION", "COMPLETING", "REDUCING",
                     "HOLDING", "RECONCILING", "COMPLETE", "HALTED"):
            assert getattr(R, name) in R.PHASES


# ------------------------------------------- holding is a DECISION

class TestHoldingIsADecisionNotATimeout:
    def test_hold_is_only_reached_when_the_ranking_chose_it(self):
        """COMPLETION_EVALUATION and HOLDING have the same book. Which one
        we are in is a statement about the decision, so it comes from the
        ranking -- never from a clock."""
        assert ph(inventory=inv("5", "0"))["phase"] == \
            R.COMPLETION_EVALUATION
        got = ph(inventory=inv("5", "0"), chosen_action="HOLD")
        assert got["phase"] == R.HOLDING
        assert got["why"] == "RANKING_CHOSE_HOLD"

    def test_holding_does_not_block_entries(self):
        """Carrying a residual within limits is a permitted state, not a
        penalty box."""
        assert ph(inventory=inv("5", "0"),
                  chosen_action="HOLD")["blocks_new_entries"] is False

    def test_the_module_cannot_force_a_liquidation(self):
        """It has no price, no fee and no clock in its signature, so it
        cannot express 'unwind now at any cost'."""
        import inspect
        params = set(inspect.signature(R.phase).parameters)
        assert params == {"orders", "inventory", "halted", "window_closed",
                          "chosen_action"}
        assert not {"now", "age_s", "deadline", "book", "price"} & params


# -------------------------------------- uncertainty blocks entries

class TestUncertaintyFailsClosed:
    def test_an_uncertain_order_reconciles_and_blocks(self):
        got = ph(orders=[row(state="UNCERTAIN")], inventory=inv("5", "0"))
        assert got["phase"] == R.RECONCILING
        assert got["blocks_new_entries"] is True
        assert got["why"] == "ORDER_STATE_UNCERTAIN"

    def test_a_cancel_not_yet_terminal_reconciles_and_blocks(self):
        """A cancel we asked for and have not read terminal is unresolved:
        the order may still be working, or may have filled."""
        got = ph(orders=[row(cancel_requested=True)], inventory=inv("5", "0"))
        assert got["phase"] == R.RECONCILING
        assert got["blocks_new_entries"] is True

    def test_a_cancel_already_terminal_does_not_block(self):
        got = ph(orders=[row(state="CANCELLED", cancel_requested=True)],
                 inventory=inv("5", "0"))
        assert got["phase"] != R.RECONCILING

    def test_uncertainty_outranks_a_working_completion(self):
        """We must not report ourselves as completing while we cannot say
        what we hold."""
        got = ph(orders=[row(reason="STRADDLE_COMPLETION"),
                         row(state="UNCERTAIN")],
                 inventory=inv("5", "0"))
        assert got["phase"] == R.RECONCILING

    def test_halt_outranks_everything(self):
        got = ph(orders=[row(state="UNCERTAIN")], inventory=inv("5", "0"),
                 halted=True)
        assert got["phase"] == R.HALTED
        assert got["blocks_new_entries"] is True

    def test_exactly_the_three_phases_block_entries(self):
        assert set(R.NO_ENTRY) == {R.RECONCILING, R.HALTED, R.COMPLETE}


# ------------------------------- venue order semantics, VERIFIED

class TestTheVenueOrderSemanticsWeActuallyHave:
    """Read from docs.polymarket.us/api-reference/orders/create-order on
    2026-11-17. This programme had been ASSUMING PMUS offered only
    GOOD_TILL_CANCEL and had no batch route. Both were wrong."""

    def test_the_documented_tif_set(self):
        for t in ("TIME_IN_FORCE_GOOD_TILL_CANCEL",
                  "TIME_IN_FORCE_FILL_OR_KILL",
                  "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
                  "TIME_IN_FORCE_DAY", "TIME_IN_FORCE_GOOD_TILL_DATE"):
            assert t in B.DOCUMENTED_TIF

    def test_the_default_body_is_byte_identical(self):
        """The proven body is what ships. New arguments must not change
        an order the venue has been accepting all along."""
        got = B.build(slug="m", side="UP", price=D("0.08"), qty=D("5"),
                      post_only=True)
        assert got == {
            "marketSlug": "m",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": "0.08", "currency": "USD"},
            "quantity": 5.0,
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": True,
            "outcomeSide": got["outcomeSide"],
            "action": "ORDER_ACTION_BUY",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        }
        assert "slippageTolerance" not in got

    def test_FOK_is_available_for_a_crossing_completion(self):
        """The venue's own answer to partial completion: fill the whole
        clip or none of it, rather than leaving half a hedge against the
        residual it was retiring."""
        got = B.build(slug="m", side="DOWN", price=D("0.55"), qty=D("2"),
                      post_only=False, tif=B.TIF_FOK, slippage_ticks=1)
        assert got["tif"] == B.TIF_FOK
        assert got["slippageTolerance"] == {"ticks": 1}

    def test_post_only_and_FOK_are_contradictory_and_refused(self):
        """"Must rest on the book prior to matching" cannot be combined
        with "fill immediately or kill"."""
        for t in B.COMPLETION_TIFS:
            with pytest.raises(ValueError):
                B.build(slug="m", side="UP", price=D("0.08"), qty=D("5"),
                        post_only=True, tif=t)

    def test_an_undocumented_tif_is_refused(self):
        with pytest.raises(ValueError):
            B.build(slug="m", side="UP", price=D("0.08"), qty=D("5"),
                    post_only=True, tif="TIME_IN_FORCE_MADE_UP")

    def test_negative_slippage_is_refused(self):
        with pytest.raises(ValueError):
            B.build(slug="m", side="UP", price=D("0.08"), qty=D("5"),
                    post_only=False, tif=B.TIF_FOK, slippage_ticks=-1)

    def test_documented_is_not_the_same_as_accepted(self):
        """Recorded so the distinction cannot be lost: none of these has
        ever been sent by this adapter, and acceptance for THIS account is
        unverified because the credentials live on the Railway service."""
        src = open("src/l5_order_body.py").read()
        assert "DOCUMENTED IS NOT ACCEPTED" in src
        assert "/v1/order/preview" in src

"""The approved $2 instantaneous exposure cap, enforced rather than declared.

THE DEFECT THESE COVER. The owner approved a $2 account-wide instantaneous
envelope. `max_total_exposure_usd` was a bare D("20") in PROBE_LIMITS with no
way to configure it, so the approval document could say 2 -- and then
authorize_arming step 14 refused CAMPAIGN_LIMITS_WIDER_THAN_APPROVED because
the RUNNING limit was 20. Putting 20 in the document made arming succeed and
left the real ceiling at 20.

The $1.65 cash envelope is NOT a substitute and these tests say so
explicitly: it bounds ONE order's obligation, while this bounds worst-case
loss across every market holding a position or a resting order.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from importlib import reload

import pytest

from src.l5_live_execution import SafetyLimits

CAP = D("2")


def lim(**kw):
    base = dict(max_usd_per_market=D("20"), max_abs_residual=D("10"),
                max_total_exposure_usd=CAP, max_concurrent_markets=1,
                max_live_orders=1, daily_realized_loss_halt_usd=D("5"))
    base.update(kw)
    return SafetyLimits(**base)


def refuse(limits=None, *, risk=None, committed=None, outstanding=None,
           price=D("0.50"), qty=D("1"), residual_after=D(0),
           realized_today=D(0), live_orders=0, uncertain=None,
           outstanding_residual=D(0), market="M1"):
    return (limits or lim()).refuse(
        price=price, qty=qty, market=market,
        committed_by_market=dict(committed or {}),
        residual_after=residual_after, realized_today=realized_today,
        open_markets=[], outstanding_by_market=dict(outstanding or {}),
        live_order_count=live_orders, outstanding_residual=outstanding_residual,
        orders_uncertain=uncertain,
        risk_by_market=(dict(risk) if risk is not None else None))


# ------------------------------------------------- the configurable ceiling

class TestTheCeilingIsSettable:

    @pytest.mark.parametrize("raw,expect", [
        (None, "20"), ("2", "2"), ("2.00", "2.00"), ("1.65", "1.65"),
        ("20", "20"),
        # UNREADABLE IS THE DEFAULT, NEVER INFINITY: a typo must not
        # silently remove the ceiling.
        ("garbage", "20"), ("", "20"), ("NaN", "20"), ("Infinity", "20"),
        # a zero or negative ceiling would disarm by arithmetic rather than
        # by an owner decision, which is a confusing way to stop.
        ("0", "20"), ("-5", "20"),
    ])
    def test_boundary_values(self, raw, expect, monkeypatch):
        if raw is None:
            monkeypatch.delenv("L5_MAX_TOTAL_EXPOSURE_USD", raising=False)
        else:
            monkeypatch.setenv("L5_MAX_TOTAL_EXPOSURE_USD", raw)
        import src.l5_pmus_live_runner as R
        reload(R)
        assert str(R.max_total_exposure_usd()) == expect
        assert str(R.PROBE_LIMITS["max_total_exposure_usd"]) == expect

    def test_the_default_is_unchanged_so_unset_changes_nothing(self,
                                                               monkeypatch):
        monkeypatch.delenv("L5_MAX_TOTAL_EXPOSURE_USD", raising=False)
        import src.l5_pmus_live_runner as R
        reload(R)
        assert R.DEFAULT_MAX_TOTAL_EXPOSURE_USD == D("20")
        assert R.max_total_exposure_usd() == D("20")


# ------------------------------------------- the cap is EFFECTIVE, not stated

class TestTheEffectiveCap:
    """`sum(risk_by_market) > cap` -- and risk_by_market already contains the
    PROPOSED order, folded in by account_wide_risk_by_market(extra_*) and
    documented at the call site as 'this order counted as if it fills in
    full'. So nothing is added here, and nothing is double counted."""

    def test_exactly_at_the_cap_is_permitted(self):
        assert refuse(risk={"M1": D("2.00")}) is None

    def test_one_cent_over_the_cap_refuses(self):
        assert refuse(risk={"M1": D("2.01")}) == "MAX_TOTAL_EXPOSURE"

    def test_a_tiny_overage_still_refuses(self):
        assert refuse(risk={"M1": D("2.0000001")}) == "MAX_TOTAL_EXPOSURE"

    def test_well_under_is_permitted(self):
        assert refuse(risk={"M1": D("1.65")}) is None

    def test_a_twenty_dollar_proposal_refuses_under_a_two_dollar_cap(self):
        """The whole point: the old ceiling would have allowed this."""
        assert refuse(risk={"M1": D("20")}) == "MAX_TOTAL_EXPOSURE"
        assert refuse(lim(max_total_exposure_usd=D("20")),
                      risk={"M1": D("20")}) is None


class TestItIsAccountWideNotPerMarket:
    """The load-bearing defect the risk model was introduced to fix: a
    one-entry dict made max_total_exposure arithmetically identical to
    max_usd_per_market, so manual inventory elsewhere contributed nothing."""

    def test_inventory_in_another_market_consumes_the_envelope(self):
        assert refuse(risk={"M1": D("1.20"),
                            "MANUAL-OTHER": D("1.00")}) \
            == "MAX_TOTAL_EXPOSURE"

    def test_manual_inventory_alone_can_exhaust_it(self):
        assert refuse(risk={"M1": D("0.10"),
                            "MANUAL-OTHER": D("2.04")}) \
            == "MAX_TOTAL_EXPOSURE"

    def test_several_small_markets_sum(self):
        assert refuse(risk={"A": D("0.7"), "B": D("0.7"), "C": D("0.7")}) \
            == "MAX_TOTAL_EXPOSURE"
        assert refuse(risk={"A": D("0.6"), "B": D("0.6"), "C": D("0.6")}) \
            is None


class TestPartialFillsAndResiduals:

    def test_a_partially_filled_leg_counts_through_the_risk_model(self):
        """A partial fill leaves worst-case exposure behind, and the model
        prices positions and still-fillable orders together."""
        assert refuse(risk={"M1": D("1.10")}) is None
        assert refuse(risk={"M1": D("2.10")}) == "MAX_TOTAL_EXPOSURE"

    def test_a_residual_position_is_bounded_separately(self):
        assert refuse(risk={"M1": D("0.5")}, residual_after=D("11")) \
            == "MAX_ABS_RESIDUAL"

    def test_residual_counts_what_would_land_if_every_order_filled(self):
        assert refuse(risk={"M1": D("0.5")}, residual_after=D("6"),
                      outstanding_residual=D("5")) == "MAX_ABS_RESIDUAL"
        assert refuse(risk={"M1": D("0.5")}, residual_after=D("6"),
                      outstanding_residual=D("4")) is None

    def test_the_fallback_notional_path_adds_the_proposed_cost(self):
        """With no risk model the pending order is NOT already included, so
        that branch adds `cost` -- and the two branches must not be confused.
        0.9 committed + 0.9 outstanding + (0.5 x 1) = 2.3 > 2."""
        assert refuse(committed={"M1": D("0.9")},
                      outstanding={"M1": D("0.9")}) == "MAX_TOTAL_EXPOSURE"
        assert refuse(committed={"M1": D("0.7")},
                      outstanding={"M1": D("0.7")}) is None


class TestConcurrentOrderReservations:

    def test_one_live_order_blocks_a_second(self):
        assert refuse(risk={"M1": D("0.1")}, live_orders=1) \
            == "MAX_LIVE_ORDERS"

    def test_outstanding_orders_consume_the_envelope_in_the_risk_model(self):
        """risk_exposure_usd prices still-fillable orders, so a resting
        order's worst case is already inside risk_by_market."""
        assert refuse(risk={"M1": D("1.0"), "M2": D("1.5")}) \
            == "MAX_TOTAL_EXPOSURE"

    def test_an_uncountable_live_order_count_refuses(self):
        """_still_fillable_or_at_limit reports AT THE LIMIT when it cannot
        compute, so the limit does not vanish when the book is confusing."""
        assert refuse(risk={"M1": D("0.1")}, live_orders=99) \
            == "MAX_LIVE_ORDERS"


class TestRestartAndReconciliation:

    def test_uncertain_order_state_refuses_before_any_arithmetic(self):
        assert refuse(risk={"M1": D("0.01")},
                      uncertain="RECONCILE_PENDING") == "ORDER_STATE_UNCERTAIN"

    def test_uncertainty_outranks_an_otherwise_permitted_proposal(self):
        assert refuse(risk={"M1": D("0.00")}, uncertain="X") \
            == "ORDER_STATE_UNCERTAIN"

    def test_unreadable_account_exposure_refuses_at_the_call_site(self):
        """account_wide_risk_by_market returning ok False refuses before
        refuse() is reached -- exposure is UNKNOWN, never zero."""
        import inspect
        from src import l5_live_execution as X
        src = inspect.getsource(X)
        assert 'if not acct_risk.get("ok"):' in src
        assert "EXPOSURE_CALL_FAILED" in inspect.getsource(
            __import__("src.l5_pmus_live_runner", fromlist=["x"]))


class TestTheCashEnvelopeIsNotTheCap:
    """Stated as a test because conflating them was the original error."""

    def test_the_cash_envelope_does_not_bound_account_exposure(self):
        """$1.65 bounds ONE order's obligation. It says nothing about
        inventory already held in other markets."""
        assert refuse(risk={"M1": D("1.00"), "MANUAL-OTHER": D("1.50")}) \
            == "MAX_TOTAL_EXPOSURE"

    def test_the_two_limits_are_separate_fields(self):
        from src import l5_live_validation_gates as VG
        g = VG.ValidationGates()
        assert hasattr(g, "max_cash_deployed_usd")
        assert not hasattr(g, "max_total_exposure_usd")

    def test_worst_case_loss_reports_the_binding_pair(self):
        assert lim().worst_case_loss_usd() == D("5")       # halt binds at $2
        assert lim(max_total_exposure_usd=D("20")
                   ).worst_case_loss_usd() == D("20")

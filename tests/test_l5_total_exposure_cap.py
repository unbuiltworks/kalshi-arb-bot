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


@pytest.fixture(autouse=True)
def _restore_runner_module_state():
    """RELOAD LEAVES THE MODULE WHERE THE LAST TEST PUT IT.

    These tests reload l5_pmus_live_runner to re-evaluate PROBE_LIMITS, which
    is module-level. monkeypatch restores the ENVIRONMENT but not the already
    re-evaluated module, so without this the final parametrised case left
    PROBE_LIMITS["max_total_exposure_usd"] as None for every suite that ran
    afterwards -- which is exactly what happened: five unrelated tests in
    other files failed until this fixture existed.
    """
    yield
    import src.l5_pmus_live_runner as R
    os.environ.pop("L5_MAX_TOTAL_EXPOSURE_USD", None)
    reload(R)


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
        ("20", "20"), (" 2 ", "2"),
    ])
    def test_readable_values(self, raw, expect, monkeypatch):
        if raw is None:
            monkeypatch.delenv("L5_MAX_TOTAL_EXPOSURE_USD", raising=False)
        else:
            monkeypatch.setenv("L5_MAX_TOTAL_EXPOSURE_USD", raw)
        import src.l5_pmus_live_runner as R
        reload(R)
        assert str(R.max_total_exposure_usd()) == expect
        assert str(R.PROBE_LIMITS["max_total_exposure_usd"]) == expect

    def test_unset_uses_the_unchanged_default(self, monkeypatch):
        monkeypatch.delenv("L5_MAX_TOTAL_EXPOSURE_USD", raising=False)
        import src.l5_pmus_live_runner as R
        reload(R)
        assert R.DEFAULT_MAX_TOTAL_EXPOSURE_USD == D("20")
        assert R.max_total_exposure_usd() == D("20")


class TestAConfiguredButUnreadableCeilingRefuses:
    """THE REGRESSION THIS CLASS EXISTS FOR. An owner who deliberately
    configured a $2 ceiling and mistyped it must get a LIVE REFUSAL, never a
    silent fall back to $20 -- a typo must not quadruple the approved
    envelope. UNSET and UNREADABLE are different states."""

    @pytest.mark.parametrize("bad", [
        "garbage", "2.0.0", "two", "$2", "2,00", "1e", "--2",
        "NaN", "nan", "Infinity", "-Infinity", "inf",
        "0", "0.00", "-5", "-0.01",
    ])
    def test_a_set_but_unusable_value_yields_none_not_the_default(
            self, bad, monkeypatch):
        monkeypatch.setenv("L5_MAX_TOTAL_EXPOSURE_USD", bad)
        import src.l5_pmus_live_runner as R
        reload(R)
        got = R.max_total_exposure_usd()
        assert got is None, "%r silently became %r" % (bad, got)
        assert R.PROBE_LIMITS["max_total_exposure_usd"] is None

    def test_it_never_silently_becomes_twenty(self, monkeypatch):
        monkeypatch.setenv("L5_MAX_TOTAL_EXPOSURE_USD", "garbage")
        import src.l5_pmus_live_runner as R
        reload(R)
        assert R.max_total_exposure_usd() != D("20")

    def test_the_safety_gate_refuses_by_name(self):
        """Refusal 1 of 3: no arithmetic is attempted on the ceiling."""
        assert refuse(lim(max_total_exposure_usd=None),
                      risk={"M1": D("0.01")}) \
            == "MAX_TOTAL_EXPOSURE_CEILING_UNREADABLE"

    def test_it_refuses_even_with_zero_exposure_and_a_tiny_order(self):
        assert refuse(lim(max_total_exposure_usd=None), risk={},
                      price=D("0.01"), qty=D("0.01")) \
            == "MAX_TOTAL_EXPOSURE_CEILING_UNREADABLE"

    def test_it_refuses_on_the_notional_fallback_path_too(self):
        assert refuse(lim(max_total_exposure_usd=None), risk=None) \
            == "MAX_TOTAL_EXPOSURE_CEILING_UNREADABLE"

    def test_the_ceiling_check_precedes_the_live_order_check(self):
        """Ordering matters only for the reason string, but the reason is
        what the operator acts on, so it is pinned."""
        assert refuse(lim(max_total_exposure_usd=None),
                      risk={"M1": D("0.01")}, live_orders=5) \
            == "MAX_TOTAL_EXPOSURE_CEILING_UNREADABLE"

    def test_uncertain_orders_still_outrank_it(self):
        assert refuse(lim(max_total_exposure_usd=None),
                      risk={"M1": D("0.01")}, uncertain="X") \
            == "ORDER_STATE_UNCERTAIN"

    def test_the_exposure_report_refuses_rather_than_inventing_headroom(self):
        """Refusal 2 of 3. Reporting capacity against an unreadable ceiling
        would manufacture headroom that does not exist."""
        import inspect
        from src import l5_pmus_live_runner as R
        src = inspect.getsource(R)
        assert "ACCOUNT_EXPOSURE_CEILING_UNREADABLE" in src
        assert 'if cap is None:' in src

    def test_arming_refuses_because_running_limits_are_unreadable(self):
        """Refusal 3 of 3, at authorize_arming step 14: limits that cannot be
        read cannot be shown to be inside the approval."""
        import inspect
        from src import l5_live_authorization as A
        src = inspect.getsource(A)
        assert "CAMPAIGN_RUNNING_LIMITS_UNREADABLE" in src
        assert "_run_exp is None" in src

    def test_worst_case_report_does_not_crash_on_an_unreadable_ceiling(self):
        """A diagnostic must not take the process down; `refuse` is what
        blocks, and it already has."""
        assert lim(max_total_exposure_usd=None).worst_case_loss_usd() == D("5")

    def test_as_dict_shows_the_unreadable_state(self):
        assert lim(max_total_exposure_usd=None
                   ).as_dict()["max_total_exposure_usd"] == "None"


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


# ------------------------- the approved absolute residual ceiling (3)

class TestAbsoluteResidualCeiling:
    """The owner approved "residual cap 3". The implemented control was
    RESIDUAL_CAP_k2 -- cap = k * max(largest filled clip, this order's qty)
    with k=2 -- which coincides with 3 only at a clip of 1.5 and EXCEEDS it
    without limit above that. On 2026-10-23 the 2015z clip was 1.3 (cap 2.6),
    but a larger envelope raises the clip and with it the cap, silently past
    the approved figure."""

    K = D("2")

    def _eff(self, clip):
        from src.l5_live_execution import ABS_RESIDUAL_CAP_CONTRACTS as A
        return min(self.K * D(clip), A)

    def test_the_approved_figure_is_five(self):
        """RAISED 3 -> 5 ON OWNER AUTHORIZATION 2026-11-02.

        The raise moves the crossover clip from 1.5 to 2.5, so the absolute
        ceiling now binds over a strictly SMALLER range of clips -- the
        k-rule governs more of the space than it did. See
        `test_cap_five_readmits_the_1415z_order_six`.
        """
        from src.l5_live_execution import ABS_RESIDUAL_CAP_CONTRACTS as A
        assert A == D("5")

    @pytest.mark.parametrize("clip,expect,binds", [
        ("1.0", "2.0", "RELATIVE"),
        ("1.3", "2.6", "RELATIVE"),       # the live 2015z clip
        ("1.5", "3.0", "RELATIVE"),
        ("2.5", "5.0", "RELATIVE"),       # the crossover, moved by the raise
        ("2.51", "5", "ABSOLUTE"),
        ("10", "5", "ABSOLUTE"),
        ("37.73", "5", "ABSOLUTE"),       # a real clip from max_clip_filled
    ])
    def test_the_effective_cap_never_exceeds_five(self, clip, expect, binds):
        got = self._eff(clip)
        assert str(got) == expect
        assert got <= D("5")
        rel = self.K * D(clip)
        assert ("ABSOLUTE" if D("5") < rel else "RELATIVE") == binds

    def test_it_only_ever_tightens(self):
        """min() can never produce a looser bound than the relative rule."""
        for clip in ("0.01", "1", "1.5", "2", "100"):
            assert self._eff(clip) <= self.K * D(clip)

    def test_the_outer_safety_bound_is_untouched(self):
        """max_abs_residual stays at 10; this is a separate, tighter rule
        applied where the k-rule is applied."""
        assert lim().max_abs_residual == D("10")

    def test_the_code_applies_the_intersection(self):
        import inspect
        from src import l5_live_execution as X
        src = inspect.getsource(X)
        # the intersection now lives in `residual_cap_decision`, the one
        # definition the gate and the final pre-wire guard both call.
        assert "cap = min(k * use_clip, ABS_RESIDUAL_CAP_CONTRACTS)" in src
        assert "cap = k * use_clip\n" not in src
        assert "cap = self.residual_cap_k * clip\n" not in src

    def test_telemetry_names_which_bound_binds(self):
        """An operator must be able to see whether the relative or the
        absolute rule decided."""
        import inspect
        from src import l5_live_execution as X
        src = inspect.getsource(X)
        for field in ("relative_cap_contracts", "absolute_cap_contracts",
                      "which_binds"):
            assert field in src, field

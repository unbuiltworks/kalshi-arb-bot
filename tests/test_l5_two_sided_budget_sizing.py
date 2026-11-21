"""The planner must never emit a plan its own campaign budget must reject.

THE DEFECT, measured in production on 2026-10-22 in window
cpc-btc-updown-15m-2026-10-22-2045z. `build_orders` sized against the cash
envelope and the loss ceiling only, and produced:

    pair_cost 0.9500   qty_per_leg 3.36   cash_gate_obligation_usd 4.636800

which FITS the $4.65 cash gate with 1.3 cents to spare. Gate 3b then had to
refuse it, because $8.8988 of account-wide realized loss already stood
against the $11.10 campaign budget:

    8.8988 + 4.6368 = 13.5356 > 11.10

A refusal the planner could have avoided spends a window reservation for
nothing, and `WindowLedger.reserve` is deliberately not refundable. The same
module's own comment records it learning this identical lesson one gate
earlier, about the cash basis: "Sizing on `pair` would therefore build a plan
the gate then refuses -- which is exactly what happened the first time this
was wired."

THE OBLIGATION BASIS IS SHARED, which is what makes the fix correct rather
than merely smaller. Gate 3b charges an order exactly as the cash gate does:
a resting DOWN leg is a BUY_SHORT worth a dollar a share, so the obligation
is (1 + up_price) per contract. Sizing the budget on any other basis would
reproduce the bug in the other direction.

CORRECTION TO THE RECORD. An earlier commit attributed this refusal to
~$4.17 of stale retired-market residue. That was wrong: `cash_committed` was
zero throughout, as the authenticated venue read confirms (positions {},
openOrders 0, assetNotional 0). The whole 13.5348-13.5488 range observed in
production is 8.8988 plus the pair's own obligation as the planner re-sized
against the $4.65 cap. The retired-market asymmetry fixed in 9105c386 is
real and still worth having, but it was not this.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS


@pytest.fixture(autouse=True)
def authorized(monkeypatch):
    """`active()` is inert without BOTH an experiment id and a market pattern.

    Set here rather than worked around: the planner refuses
    NOT_THE_AUTHORIZED_MARKET by design, and a test that bypassed that gate
    would be testing a configuration production cannot reach.
    """
    # the trailing `*` matters: an entry with no wildcard is an EXACT slug
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-btc-updown-15m-*")
    monkeypatch.setenv("L5_TS_EXPERIMENT_ID", "budget-sizing-test")
    return monkeypatch

#: the production book for cpc-btc-updown-15m-2026-10-22-2045z
BOOK = dict(bid="0.4000", ask="0.5900",
            bid_depth="3815.9700", ask_depth="110.0100",
            tick="0.01", min_qty="0.01", depth_ticks=2, book_age_s="0.158")

CASH = D("4.65")          # max_cash_deployed_usd, verified in container
LOSS = D("10")            # max_abs_residual-derived ceiling, not binding here
REALIZED = D("8.8988")    # account-wide realized loss charged by gate 3b
BUDGET = D("11.10")       # campaign budget
HEADROOM = BUDGET - REALIZED      # 2.2012


def plan(market="cpc-btc-updown-15m-2026-10-22-2045z", **kw):
    args = dict(market=market, cash_envelope=CASH, max_worst_case_loss=LOSS)
    args.update(BOOK)
    args.update(kw)
    return TS.build_orders(**args)


def oblig(p):
    return D(p["qty_per_leg"]) * (D(1) + D(p["legs"][0]["price"]))


class TestTheProductionRefusalIsGone:

    def test_without_a_budget_it_reproduces_the_oversized_plan(self):
        """The pre-fix behaviour, pinned so the regression is visible.

        Unconstrained, the planner still sizes to the cash gate -- which is
        correct for a caller running no campaign.
        """
        p = plan()
        assert p["ok"] is True, p
        assert D(p["cash_gate_obligation_usd"]) <= CASH
        # and this is the plan the campaign budget would have had to refuse
        assert REALIZED + D(p["cash_gate_obligation_usd"]) > BUDGET

    def test_with_the_budget_the_plan_fits_both_gates(self):
        p = plan(budget_headroom=HEADROOM)
        assert p["ok"] is True, p
        assert oblig(p) <= HEADROOM, (p["qty_per_leg"], str(oblig(p)))
        assert oblig(p) <= CASH
        # the figure gate 3b will compute is now inside the budget
        assert REALIZED + oblig(p) <= BUDGET

    def test_the_budget_is_reported_as_the_binding_constraint(self):
        p = plan(budget_headroom=HEADROOM)
        assert p["sized_by"] == "CAMPAIGN_BUDGET"
        assert p["budget_headroom_usd"] == str(HEADROOM)

    def test_it_is_strictly_smaller_than_the_cash_sized_plan(self):
        """Not merely different -- the budget binds tighter here."""
        assert (D(plan(budget_headroom=HEADROOM)["qty_per_leg"])
                < D(plan()["qty_per_leg"]))

    def test_the_quantity_stays_on_the_venue_grid(self):
        """0.01 is the venue's own orderMinQuantity, read from the market."""
        q = D(plan(budget_headroom=HEADROOM)["qty_per_leg"])
        assert q % D("0.01") == 0, q
        assert q >= D("0.01")


class TestTheConstraintsCompose:

    def test_a_generous_budget_does_not_loosen_the_cash_gate(self):
        """A third constraint may only ever REFUSE more, never permit more."""
        p = plan(budget_headroom=D("10000"))
        assert p["ok"] is True
        assert oblig(p) <= CASH
        assert D(p["qty_per_leg"]) == D(plan()["qty_per_leg"])
        assert p["sized_by"] != "CAMPAIGN_BUDGET"

    def test_a_tiny_budget_sizes_down_to_the_venue_minimum(self):
        p = plan(budget_headroom=D("0.05"))
        if p["ok"]:
            assert oblig(p) <= D("0.05")
            assert D(p["qty_per_leg"]) >= D("0.01")
        else:
            assert p["reason"] == "NO_LEGAL_QUANTITY"
            assert p["binding"] == "CAMPAIGN_BUDGET"

    def test_a_budget_below_one_grid_step_refuses_and_names_the_budget(self):
        """It must not be misreported as a book or depth problem."""
        p = plan(budget_headroom=D("0.001"))
        assert p["ok"] is False
        assert p["reason"] == "NO_LEGAL_QUANTITY"
        assert p["binding"] == "CAMPAIGN_BUDGET"
        assert p["budget_headroom_usd"] == "0.001"

    def test_an_exhausted_budget_refuses_outright(self):
        for v in (D("0"), D("-1.25")):
            p = plan(budget_headroom=v)
            assert p["ok"] is False
            assert p["reason"] == "CAMPAIGN_BUDGET_EXHAUSTED", (v, p)

    def test_none_means_not_supplied_and_never_zero(self):
        """The distinction that keeps a non-campaign caller working."""
        assert plan(budget_headroom=None)["ok"] is True
        assert plan(budget_headroom=D("0"))["ok"] is False


class TestTheHeadroomHelperMirrorsTheGate:

    def test_an_unreadable_realized_figure_yields_no_headroom(self):
        """"I could not tell" and "nothing spent" differ by the whole budget.

        Reading an unreadable realized loss as zero would hand the planner
        the entire budget at exactly the moment the accounting is broken.
        """
        from src import l5_live_execution as E

        class Budget:
            budget_usd = D("11.10")

        class Gates:
            campaign_budget = Budget()
            max_cash_deployed_usd = D("4.65")

            def cash_committed(self, legs):
                return D(0)

            def pending_obligation(self, out):
                return D(0)

        class Store:
            def realized_today(self):
                return None            # UNREADABLE

        ex = object.__new__(E.FaithfulExecutor)
        ex.validation_gates = Gates()
        ex.store = Store()
        ex.cash_legs_for_gates = lambda: []
        ex.outstanding_for_gates = lambda: []
        assert ex.probe_budget_headroom() == D(0)

    def test_no_campaign_budget_means_unconstrained_not_zero(self):
        from src import l5_live_execution as E

        class Gates:
            campaign_budget = None

        ex = object.__new__(E.FaithfulExecutor)
        ex.validation_gates = Gates()
        assert ex.probe_budget_headroom() is None

    def test_the_realized_loss_is_subtracted_on_the_gates_own_basis(
            self, tmp_path):
        """The gate's basis is now CAMPAIGN-SCOPED, and this helper still
        mirrors it -- which is the whole point of the class.

        The zero point is captured at realized 0, which makes the scoped
        basis `max(0, 0 - now) + commitments` arithmetically identical to
        the lifetime `max(0, -now) + commitments` this test was written
        against, so the expected figure is unchanged. What is pinned is the
        MIRRORING, not which basis the gate happens to use.
        """
        from src import l5_campaign_baseline as CBL
        from src import l5_live_execution as E

        _b = CBL.CampaignBaseline(root=str(tmp_path))
        assert _b.capture("camp1.sizing", account_wide_realized="0")["ok"]

        class Budget:
            budget_usd = D("11.10")

        class Gates:
            campaign_budget = Budget()
            campaign_baseline = _b
            campaign_id = "camp1.sizing"

            def cash_committed(self, legs):
                return D(0)

            def pending_obligation(self, out):
                return D(0)

        class Store:
            def realized_today(self):
                return D("-8.8988")

        ex = object.__new__(E.FaithfulExecutor)
        ex.validation_gates = Gates()
        ex.store = Store()
        ex.cash_legs_for_gates = lambda: []
        ex.outstanding_for_gates = lambda: []
        assert ex.probe_budget_headroom() == D("2.2012")

    def test_a_realized_GAIN_does_not_create_headroom(self, tmp_path):
        """max(0, baseline - realized): a gain is not a loss that did not
        happen, and it is not headroom either. Holds on the campaign-scoped
        basis exactly as it held on the lifetime one."""
        from src import l5_campaign_baseline as CBL
        from src import l5_live_execution as E

        _b = CBL.CampaignBaseline(root=str(tmp_path))
        assert _b.capture("camp1.sizing", account_wide_realized="0")["ok"]

        class Budget:
            budget_usd = D("11.10")

        class Gates:
            campaign_budget = Budget()
            campaign_baseline = _b
            campaign_id = "camp1.sizing"

            def cash_committed(self, legs):
                return D(0)

            def pending_obligation(self, out):
                return D(0)

        class Store:
            def realized_today(self):
                return D("5.00")       # a GAIN

        ex = object.__new__(E.FaithfulExecutor)
        ex.validation_gates = Gates()
        ex.store = Store()
        ex.cash_legs_for_gates = lambda: []
        ex.outstanding_for_gates = lambda: []
        assert ex.probe_budget_headroom() == D("11.10")

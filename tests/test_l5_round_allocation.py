"""Splitting the cash envelope across sequential cycles.

THE FINDING THAT MOTIVATED IT. Every plan the 20261024a campaign produced was
`sized_by: CASH_ENVELOPE` with a cash-gate obligation of 1.6366-1.6480 against
the $1.65 envelope -- 99.2% to 99.9% spent by round 0 -- while
`accumulation_max_rounds` sat at 8 and campaign headroom never fell below
$20.38 of $29.30. With `cash_policy: NO_RECYCLING` and settlement landing about
T+16 minutes, after the window closed, a second cycle was arithmetically
impossible rather than policy-refused.

THE FINDING THAT QUALIFIES IT, asserted here so it cannot be forgotten: two
half-size rounds deliver FEWER contracts than one full round, because the
venue grid floors twice instead of once. This is variance reduction at a small
capacity cost, NOT a throughput improvement, and these tests pin that.

Nothing here touches a venue, a credential or a limit.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_two_sided_probe as TS

SLUG = "cpc-btc-updown-15m-2026-10-24-0100z"

#: the six real books at submission, from the preserved campaign tapes
BOOKS = [("0100z", "0.5200", "0.5300"), ("0100z-r1", "0.6000", "0.6100"),
         ("0115z", "0.7400", "0.7500"), ("0130z", "0.3100", "0.3200"),
         ("0145z", "0.6200", "0.6300"), ("0200z", "0.4100", "0.4200")]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("L5_TS_MARKET", SLUG)
    monkeypatch.setenv("L5_TS_EXPERIMENT_ID", "round-alloc-test")
    monkeypatch.delenv("L5_TS_ROUND_ALLOCATIONS", raising=False)
    monkeypatch.delenv("L5_TS_MIN_FIRST_LEG_PRICE", raising=False)


def plan(bid, ask, envelope="1.65"):
    return TS.build_orders(
        market=SLUG, bid=bid, ask=ask, bid_depth="4000", ask_depth="4000",
        tick="0.01", min_qty="0.01", depth_ticks=2,
        cash_envelope=D(envelope), max_worst_case_loss=D("20"),
        budget_headroom=D("20.74"), book_age_s=0.05)


class TestDefaultOneIsCurrentBehaviour:

    def test_the_accessor_defaults_to_one(self):
        assert TS.round_allocations() == 1

    @pytest.mark.parametrize("raw", ["", "0", "-1", "13", "99", "two",
                                     "1.5", "nan", "}{"])
    def test_anything_malformed_or_out_of_range_reads_as_one(self,
                                                             monkeypatch, raw):
        """A divisor is a sizing decision; the safe reading of a malformed one
        is 'do what we already do', never 'guess'."""
        monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", raw)
        assert TS.round_allocations() == 1

    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8, 12])
    def test_the_permitted_range_is_honoured(self, monkeypatch, n):
        monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", str(n))
        assert TS.round_allocations() == n

    @pytest.mark.parametrize("w,bid,ask", BOOKS)
    def test_every_campaign_plan_is_unchanged_at_the_default(self, w, bid,
                                                             ask):
        out = plan(bid, ask)
        assert out["ok"] is True
        assert out["round_allocations"] == 1
        assert D(out["cash_envelope_per_round_usd"]) == D("1.65")
        assert D(out["cash_gate_obligation_usd"]) <= D("1.65")


class TestDivisorTwoOnlyEverShrinksARound:

    @pytest.mark.parametrize("w,bid,ask", BOOKS)
    def test_the_per_round_envelope_halves(self, monkeypatch, w, bid, ask):
        monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", "2")
        out = plan(bid, ask)
        assert out["ok"] is True
        assert D(out["cash_envelope_per_round_usd"]) == D("0.825")
        assert D(out["cash_envelope_total_usd"]) == D("1.65")

    @pytest.mark.parametrize("w,bid,ask", BOOKS)
    def test_each_clip_is_at_most_half_and_executable(self, monkeypatch, w,
                                                      bid, ask):
        full = D(plan(bid, ask)["qty_per_leg"])
        monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", "2")
        half = D(plan(bid, ask)["qty_per_leg"])
        assert half <= full / 2
        assert half >= D("0.01")                     # the venue minimum
        assert half == (half / D("0.01")).to_integral_value() * D("0.01")

    @pytest.mark.parametrize("w,bid,ask", BOOKS)
    def test_two_rounds_fit_inside_the_one_envelope(self, monkeypatch, w,
                                                   bid, ask):
        monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", "2")
        out = plan(bid, ask)
        assert 2 * D(out["cash_gate_obligation_usd"]) <= D("1.65")

    @pytest.mark.parametrize("w,bid,ask", BOOKS)
    def test_the_pair_cap_is_untouched(self, monkeypatch, w, bid, ask):
        monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", "2")
        out = plan(bid, ask)
        assert D(out["pair_cost"]) <= TS.max_pair_cost()
        for leg in out["legs"]:
            assert leg["mode"] == "MAKER" and leg["post_only"] is True


class TestTwoRoundsDoNotIncreaseCapacity:
    """Requirement 7, answered against the premise. Two rounds raise the ORDER
    COUNT and do not raise deliverable contracts: the grid floors twice."""

    def test_total_contracts_are_not_greater_and_are_slightly_fewer(
            self, monkeypatch):
        one = D(0)
        for _w, bid, ask in BOOKS:
            one += D(plan(bid, ask)["qty_per_leg"])
        monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", "2")
        two = D(0)
        for _w, bid, ask in BOOKS:
            two += D(plan(bid, ask)["qty_per_leg"]) * 2
        assert two <= one
        assert two / one > D("0.98")          # the loss is quantisation only
        assert two / one < D("1.00")

    def test_so_expected_pair_surplus_per_window_does_not_rise(self,
                                                               monkeypatch):
        """Pair surplus is edge-per-contract times contracts, and the campaign
        measured +0.0865/contract. Fewer contracts cannot earn more."""
        edge = D("0.0865")
        one = sum(D(plan(b, a)["qty_per_leg"]) for _w, b, a in BOOKS)
        monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", "2")
        two = sum(D(plan(b, a)["qty_per_leg"]) * 2 for _w, b, a in BOOKS)
        assert two * edge <= one * edge


class TestTheAggregateCapsStillBind:
    """Requirement 5: outstanding orders and residual inventory across rounds
    are aggregated under the EXISTING caps, which the divisor cannot raise."""

    def test_the_divisor_appears_nowhere_in_the_safety_limits(self):
        import inspect
        src = inspect.getsource(LX.SafetyLimits)
        assert "ROUND_ALLOCATIONS" not in src
        assert "round_allocations" not in src

    def test_the_account_wide_exposure_ceiling_still_refuses(self):
        lim = LX.SafetyLimits(max_total_exposure_usd=D("2"),
                              max_live_orders=1)
        assert lim.refuse(
            price=D("0.50"), qty=D("1"), market="m",
            committed_by_market={"other": D("1.90")}, residual_after=D(0),
            realized_today=D(0), open_markets=["m", "other"],
            outstanding_by_market={}, live_order_count=0
        ) is not None

    def test_a_second_order_is_still_refused_while_one_is_live(self):
        """Requirement 4: no new round until the prior order is terminal.
        max_live_orders stays 1 and the divisor does not touch it."""
        lim = LX.SafetyLimits(max_total_exposure_usd=D("2"),
                              max_live_orders=1)
        assert lim.refuse(
            price=D("0.35"), qty=D("0.5"), market="m",
            committed_by_market={}, residual_after=D(0),
            realized_today=D(0), open_markets=["m"],
            outstanding_by_market={}, live_order_count=1
        ) == "MAX_LIVE_ORDERS"

    def test_uncertain_order_state_still_refuses_before_anything_else(self):
        """Requirement 4's other half: inventory and cash must be reconciled.
        An unresolved order refuses ahead of every other check."""
        lim = LX.SafetyLimits(max_total_exposure_usd=D("2"),
                              max_live_orders=1)
        assert lim.refuse(
            price=D("0.35"), qty=D("0.5"), market="m",
            committed_by_market={}, residual_after=D(0),
            realized_today=D(0), open_markets=["m"],
            outstanding_by_market={}, live_order_count=0,
            orders_uncertain=True
        ) == "ORDER_STATE_UNCERTAIN"

    def test_the_daily_and_cumulative_halts_are_unaffected(self):
        lim = LX.SafetyLimits(max_total_exposure_usd=D("2"),
                              daily_realized_loss_halt_usd=D("5"),
                              max_live_orders=1)
        assert lim.refuse(
            price=D("0.35"), qty=D("0.5"), market="m",
            committed_by_market={}, residual_after=D(0),
            realized_today=D("-5"), open_markets=["m"],
            outstanding_by_market={}, live_order_count=0
        ) == "DAILY_REALIZED_LOSS_HALT"


class TestNoCashIsAssumedReleasedBeforeSettlement:
    """Requirement 6."""

    def test_the_obligation_basis_is_still_a_dollar_a_share(self):
        out = plan("0.5200", "0.5300")
        q = D(out["qty_per_leg"])
        up = D([l for l in out["legs"] if l["side"] == "UP"][0]["price"])
        assert D(out["cash_gate_obligation_usd"]) == q * (D(1) + up)

    def test_the_basis_text_still_says_settlement_releases_not_matching(self):
        out = plan("0.5200", "0.5300")
        assert "does NOT release cash before" in out["round_allocation_basis"]
        assert "dollar a share" in out["cash_basis"]

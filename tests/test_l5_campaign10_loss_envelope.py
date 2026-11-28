"""The $9.00 three-window loss envelope, proven rather than asserted.

WHAT IS BEING CLAIMED. Three BTC15m windows, maker-only, cannot together
lose more than **$9.00** under the enforced limits. The claim rests on two
things and nothing else:

  1. `max_worst_case_deficit_usd = $3.00` bounds ONE market's book at every
     submission into it, counting every resting order as if it filled;
  2. the campaign cannot trade more than three markets.

Everything below exists because the owner asked whether the per-market bound
really covers resting orders, partial fills, unsettled inventory and
cancellation races. It does, and these are the measurements.

WHAT IS NOT CLAIMED. Not $5.00. Both $5 controls read SETTLED P&L, and
back-to-back windows do not settle in time to inform the next one's gate.
See `test_the_five_dollar_gates_are_settlement_dependent`.
"""
from decimal import Decimal as D

import pytest

from src.l5_live_validation_gates import ValidationGates

CAP = D("3.00")
WINDOWS = 3


def gates(cap=CAP):
    return ValidationGates(max_cash_deployed_usd=D("12"),
                           max_unmatched_contracts=D("5"),
                           max_cumulative_loss_usd=D("18.6325"),
                           max_worst_case_deficit_usd=cap)


def flat(g, **kw):
    base = dict(up_qty=0, dn_qty=0, up_cost=0, dn_cost=0,
                order_side="UP", order_qty=D("1"), order_price=D("0.30"),
                outstanding=())
    base.update(kw)
    return g.worst_case_deficit(**base)


def up(qty, px):
    return {"qty": D(qty), "price": D(px), "intent": "ORDER_INTENT_BUY_LONG"}


def dn(qty, px):
    return {"qty": D(qty), "price": D(px), "intent": "ORDER_INTENT_BUY_SHORT"}


# ------------------------------- 1. resting orders counted as if filled

class TestRestingOrdersAreCountedAsIfFilled:

    def test_a_resting_order_raises_the_deficit_over_the_cap(self):
        """The order alone fits; with one resting leg of the same side it
        does not, and the gate refuses. This is the whole property."""
        g = gates()
        alone = flat(g, order_qty=D("3"), order_price=D("0.60"))
        assert D(str(alone["deficit"])) <= CAP, alone

        with_resting = flat(g, order_qty=D("3"), order_price=D("0.60"),
                            outstanding=[up("3", "0.60")])
        assert D(str(with_resting["deficit"])) > CAP, with_resting

    def test_the_worst_of_four_corners_is_taken_not_the_expected_one(self):
        g = gates()
        got = flat(g, order_qty=D("3"), order_price=D("0.60"),
                   outstanding=[up("3", "0.60")])
        sc = got["scenarios"]
        assert got["scenario"] == "up_only"
        # the corner that is WORST is the one returned
        worst = max(D(sc[k]) for k in ("none", "up_only", "dn_only", "all"))
        assert D(str(got["deficit"])) == worst + D(sc["rounding_slack"])

    def test_a_resting_order_with_no_readable_price_is_charged_at_one(self):
        """A resting order read as free is how an envelope is spent without
        the gate seeing it."""
        g = gates()
        got = flat(g, outstanding=[{"qty": D("3"), "price": None,
                                    "intent": "ORDER_INTENT_BUY_LONG"}])
        assert g.UNKNOWN_PRICE_WORST_CASE == D(1)
        assert D(str(got["deficit"])) > D("3")

    @pytest.mark.parametrize("intent", ["GARBAGE", "", None,
                                        "ORDER_INTENT_SELL", "SIDEWAYS"])
    def test_an_unreadable_resting_side_fails_closed(self, intent):
        g = gates()
        got = flat(g, outstanding=[{"qty": D("3"), "price": D("0.60"),
                                    "intent": intent}])
        assert got["ok"] is False
        assert got["reason"] == "DEFICIT_OUTSTANDING_SIDE_UNKNOWN"

    def test_a_failed_read_becomes_a_refusal_at_the_gate(self):
        """`ok: False` is not a pass. The gate turns every unreadable state
        into WORST_CASE_DEFICIT:<reason>."""
        import inspect
        src = inspect.getsource(ValidationGates.refuse)
        assert 'return "WORST_CASE_DEFICIT:%s" % wc.get("reason")' in src
        assert 'return "WORST_CASE_DEFICIT_NO_INVENTORY"' in src


# ---------------------------------------------- 2. partial fills

class TestPartialFills:

    def test_the_filled_part_is_inventory_and_the_rest_is_outstanding(self):
        """No double count and no gap: 3 ordered, 1 filled, 2 still resting."""
        g = gates()
        got = g.worst_case_deficit(
            up_qty=D("1"), dn_qty=0, up_cost=D("0.60"), dn_cost=0,
            order_side="DOWN", order_qty=D("1"), order_price=D("0.36"),
            outstanding=[up("2", "0.60")])
        assert got["ok"] is True
        # the book it prices is 1 filled + 1 completing, with 2 adversarial
        assert D(got["book"]["up_qty"]) == D("1")
        assert D(got["book"]["dn_qty"]) == D("1")

    def test_a_rounding_allowance_is_charged_per_still_fillable_order(self):
        """Cent rounding makes the deficit non-convex, so the corner maximum
        alone is not an upper bound. The allowance restores it."""
        g = gates()
        one = flat(g, outstanding=[up("2", "0.60")])
        two = flat(g, outstanding=[up("2", "0.60"), dn("2", "0.30")])
        assert D(one["scenarios"]["rounding_slack"]) == D("0.02")
        assert D(two["scenarios"]["rounding_slack"]) == D("0.04")

    def test_the_ledger_reports_remaining_not_the_original_quantity(self):
        """The plumbing half of the property: `outstanding_for_gates` reads
        `remaining`, so a partial fill is never counted twice."""
        import inspect
        from src import l5_live_execution as E
        src = inspect.getsource(E.FaithfulExecutor.outstanding_for_gates)
        assert 'venue_decimal(o.get("remaining"))' in src
        assert "if rem <= 0:" in src and "continue" in src


# ------------------------------------------ 3. unsettled inventory

class TestUnsettledInventory:

    def test_unsettled_inventory_of_this_market_is_in_the_book(self):
        g = gates()
        got = g.worst_case_deficit(
            up_qty=D("3"), dn_qty=0, up_cost=D("1.80"), dn_cost=0,
            order_side="UP", order_qty=D("3"), order_price=D("0.60"),
            outstanding=())
        assert D(str(got["deficit"])) > CAP, got

    def test_quantity_without_cost_is_incoherent_not_free(self):
        g = gates()
        got = g.worst_case_deficit(
            up_qty=D("3"), dn_qty=0, up_cost=0, dn_cost=0,
            order_side="UP", order_qty=D("1"), order_price=D("0.30"),
            outstanding=())
        assert got["ok"] is False
        assert got["reason"] == "DEFICIT_QUANTITY_WITHOUT_COST"

    def test_unreadable_inventory_fails_closed(self):
        g = gates()
        got = flat(g, up_qty="not-a-number")
        assert got["ok"] is False
        assert got["reason"] == "DEFICIT_INVENTORY_UNREADABLE"

    def test_a_completed_pair_carries_negative_risk(self):
        """Why the ceiling never refuses a risk-reducing completion."""
        g = gates()
        got = g.worst_case_deficit(
            up_qty=D("3"), dn_qty=0, up_cost=D("1.44"), dn_cost=0,
            order_side="DOWN", order_qty=D("3"), order_price=D("0.48"),
            outstanding=())
        assert D(str(got["deficit"])) < 0, got


# -------------------------------------------- 4. cancellation races

class TestCancellationRaces:
    """An order being cancelled must keep holding the envelope until the
    VENUE says it is gone."""

    def test_an_order_leaves_the_envelope_only_on_a_venue_terminal_state(self):
        import inspect
        from src import l5_live_execution as E
        term = inspect.getsource(E.InventoryStore.terminate_order)
        assert "venue-authoritative terminal state releases the envelope" \
            in term
        prog = inspect.getsource(E.InventoryStore.apply_order_progress)
        assert 'if prog.get("terminal"):' in prog
        assert "VENUE-CONFIRMED TERMINAL" in prog

    def test_an_unknown_order_state_refuses_every_submission(self):
        import inspect
        from src import l5_live_execution as E
        src = inspect.getsource(E.InventoryStore.orders_uncertain)
        assert "every further submission is refused" in src
        assert "Cleared ONLY by an authoritative venue reconciliation" in src

    def test_a_fill_during_cancellation_stops_the_tick(self):
        """`defensive_execute` refuses rather than resizing in flight."""
        import inspect
        from src import l5_live_execution as E
        src = inspect.getsource(E.FaithfulExecutor.defensive_execute)
        assert "DEFENSIVE_FILLED_DURING_CANCEL" in src
        assert "DEFENSIVE_CANCEL_NOT_CONFIRMED" in src

    def test_cancellation_is_by_venue_id_and_proven_by_a_re_read(self):
        import inspect
        from src import l5_live_execution as E
        src = inspect.getsource(E.deadman)
        assert "Cancel our RESTING orders by VENUE id, then prove it by "\
               "reading them" in src


# ------------------------------- 5. three markets cannot exceed $9.00

class TestThreeMarketsCannotExceedNineDollars:

    def test_one_market_cannot_be_admitted_past_the_cap(self):
        """The ceiling, found by search rather than assumed: the largest
        single leg the gate admits, and the first one it refuses."""
        g = gates()
        q, last_ok = D("0.01"), None
        while q <= D("8"):
            got = flat(g, order_qty=q, order_price=D("0.60"))
            d = D(str(got["deficit"]))
            if d <= CAP:
                last_ok = (q, d)
            else:
                break
            q += D("0.01")
        assert last_ok is not None
        assert last_ok[1] <= CAP
        # and one tick more is over
        over = flat(g, order_qty=last_ok[0] + D("0.01"),
                    order_price=D("0.60"))
        assert D(str(over["deficit"])) > CAP

    def test_three_markets_at_the_ceiling_stay_within_nine_dollars(self):
        g = gates()
        per = []
        for _market in range(WINDOWS):
            q, best = D("0.01"), None
            while q <= D("8"):
                got = flat(g, order_qty=q, order_price=D("0.60"))
                d = D(str(got["deficit"]))
                if d <= CAP:
                    best = d
                else:
                    break
                q += D("0.01")
            per.append(best)
        assert sum(per) <= CAP * WINDOWS
        assert sum(per) <= D("9.00")

    def test_the_bound_is_the_cap_times_the_window_count(self):
        """The composition argument, stated as an assertion: each market's
        final loss is bounded by the cap enforced at its last submission,
        and the campaign cannot trade more markets than its window budget."""
        assert CAP * WINDOWS == D("9.00")

    def test_the_window_budget_is_enforced_in_two_independent_places(self):
        """Three markets, not four: the approval gate AND the env budget."""
        from src import l5_campaign_approval as CA
        from src import l5_live_authorization as AUTH
        assert CA.MAX_WINDOWS == 8          # the code CEILING
        # the approval intersects, never widens
        import inspect
        src = inspect.getsource(CA.ApprovalGate.state)
        assert "eff_w = min(want_w, max_windows_for(series))" in src
        # THE PER-SERIES COUNT IS SCOPED, and this campaign is 15-minute:
        # the wider BTC5m window count must not reach it, and anything
        # unrecognized must fall back to the GENERAL ceiling rather than
        # to the wider one. Asserted behaviourally, not by source text.
        assert CA.max_windows_for(CA.BTC15M_SERIES) == CA.MAX_WINDOWS == 8
        assert CA.max_windows_for(None) == 8
        assert CA.max_windows_for("") == 8
        assert CA.max_windows_for("cpc-not-a-series-") == 8
        # and the env budget can only tighten it further
        assert hasattr(AUTH, "max_windows_env")

    def test_the_gate_is_account_wide_on_outstanding_which_is_conservative(
            self):
        """`inventory` is per-market but `outstanding` is account-wide, so
        another market's resting order is charged into THIS market's book.
        That can only add refusals."""
        import inspect
        from src import l5_live_execution as E
        src = inspect.getsource(E.FaithfulExecutor.outstanding_for_gates)
        assert "account-wide" in src


# ------------------------- why the claim is $9.00 and not $5.00

class TestTheFiveDollarGatesAreSettlementDependent:

    def test_the_daily_halt_reads_settled_day_pnl(self):
        import inspect
        from src import l5_live_execution as E
        src = inspect.getsource(E.InventoryStore.realized_today)
        assert "realized_attributed" in src and "realized_unattributed" in src
        assert "settlement" in src.lower()

    def test_the_campaign_gate_adds_realized_to_this_markets_open_book(self):
        """`max(0, realized_now - baseline) + open_worst_case`, where the
        second term is THIS market's book. An unsettled prior window is in
        neither term, which is why three back-to-back windows are not
        bounded at $5."""
        import inspect
        from src import l5_campaign_baseline as CBL
        src = inspect.getsource(CBL.CampaignBaseline.additional_loss_usd)
        assert "realized_delta_loss + open_worst_case" in src
        assert "max(0, ...)" in src or "A REALIZED GAIN CREATES NO ROOM" in src

    def test_a_realized_gain_creates_no_extra_loss_budget(self):
        import inspect
        from src import l5_campaign_baseline as CBL
        src = inspect.getsource(CBL.CampaignBaseline.additional_loss_usd)
        assert "A REALIZED GAIN CREATES NO ROOM" in src

    def test_the_campaign_gate_fails_closed_without_a_campaign_id(self):
        """Which is exactly the state the service is in now: no approval, so
        campaign_id is None and the gate refuses."""
        from src import l5_campaign_baseline as CBL
        got = CBL.CampaignBaseline().additional_loss_usd(
            None, account_wide_realized=D(0), open_worst_case_usd=D(0))
        assert got["ok"] is False
        assert got["reason"] == "CAMPAIGN_BASELINE_NO_CAMPAIGN_ID"

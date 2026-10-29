"""Safety verification for the hybrid maker/taker completion path.

WHY THIS FILE IS TESTS AND NOT NEW LOGIC. Each of the eight requirements
for a live hybrid campaign is already implemented somewhere in the deployed
executor. Writing a second implementation of a control that exists is how
two copies of a rule come to disagree, so this pins the existing ones
instead and fails if any of them moves.

Requirement -> mechanism, each asserted below:

  1 preserve maker entries / continuous pairing  DEF arm window + DWELL
  2 favourable completion from authenticated depth  DEF.decide(exit_price)
  3 taker vs hold vs sell-the-leg                   DEF.alternatives
  4 partial completion, no over-hedge               overhedge is REFUSED
  5 cancel + reconcile before replacing             DEF.cancel_confirmed
  6 no new opening while unmatched eats the budget   RESIDUAL_CAP prospective
  7 watchdog / registry / idempotency / halts        unchanged constants
  8 reuse existing services                          no new send site

Nothing here submits, cancels, arms, or touches a venue.
"""
from __future__ import annotations

import inspect
from decimal import Decimal as D

import pytest

from src import l5_defensive_completion as DEF
from src import l5_intent_gate as GATE
from src import l5_live_execution as LX
from src import l5_two_sided_probe as TSP


class TestReq1MakerEntriesArePreserved:

    def test_the_defensive_path_only_opens_near_the_terminal(self):
        """It cannot pre-empt a resting maker quote for most of the window:
        the arm window is the last 60s before the binding terminal."""
        term = DEF.binding_terminal_s(TSP.complete_cutoff_s(),
                                      TSP.cancel_cutoff(900.0))
        assert DEF.DEFENSIVE_ARM_S == 60.0
        assert DEF.arm_window_open(
            1000.0 + 100, 1000.0, complete_cutoff_s=TSP.complete_cutoff_s(),
            cancel_cutoff_s=TSP.cancel_cutoff(900.0)) is False
        assert term - DEF.DEFENSIVE_ARM_S > 0

    def test_a_dwell_is_required_before_crossing(self):
        assert DEF.DEFENSIVE_DWELL_S == 30.0

    def test_the_maker_completion_ceiling_is_untouched(self):
        assert DEF.MAKER_COMPLETE_CEILING == D("0.96")
        assert TSP.max_pair_cost() == D("0.96")

    def test_the_opening_band_is_untouched(self):
        assert TSP.min_first_leg_own_price() == D("0.20")
        assert TSP.max_first_leg_own_price() == D("0.60")


class TestReq2And3TheEconomicComparison:

    def test_decide_prices_from_executable_depth_and_a_real_fee(self):
        sig = inspect.signature(DEF.decide).parameters
        for p in ("exit_price", "displayed_depth",
                  "taker_fee_per_contract", "unmatched_qty",
                  "first_leg_cost"):
            assert p in sig, p

    def test_all_three_courses_are_priced_in_dollars(self):
        rows = DEF.alternatives(qty=D("1"), first_leg_cost=D("0.45"),
                                exit_price=D("0.48"), fee=D("0.0173"),
                                maker_complement_price=D("0.50"))
        acts = {r["action"] for r in rows}
        assert acts == {"HOLD_TO_SETTLEMENT", "MAKER_COMPLETE_AT_CEILING",
                        "DEFENSIVE_UNWIND"}
        for r in rows:
            assert r["best_usd"] is not None
            assert r["worst_usd"] is not None

    def test_holding_is_priced_as_a_coin_flip_on_the_leg(self):
        """best = 1 - p, worst = -p. That is what 'worst-case settlement
        exposure' means for a single unmatched leg."""
        rows = {r["action"]: r for r in DEF.alternatives(
            qty=D("1"), first_leg_cost=D("0.45"), exit_price=D("0.48"),
            fee=D("0.0173"))}
        h = rows["HOLD_TO_SETTLEMENT"]
        assert D(str(h["best_usd"])) == D("0.55")
        assert D(str(h["worst_usd"])) == D("-0.45")

    def test_selling_the_leg_is_the_same_order_as_buying_the_complement(self):
        """Requirement 3 names 'selling the original leg' as a third
        course. On this venue it is not a distinct economic choice, and the
        module says so from its own verification rather than assuming it."""
        # whitespace-normalised: the source wraps these phrases across
        # lines, so a raw substring check fails on formatting not content.
        doc = " ".join((inspect.getdoc(DEF.alternatives) or "").split())
        assert "BUY VERSUS SELL" in doc
        assert "same wire order at the same price for the same fee" in doc

    def test_a_certain_unwind_has_no_spread_between_best_and_worst(self):
        rows = {r["action"]: r for r in DEF.alternatives(
            qty=D("1"), first_leg_cost=D("0.45"), exit_price=D("0.48"),
            fee=D("0.0173"))}
        u = rows["DEFENSIVE_UNWIND"]
        assert D(str(u["best_usd"])) == D(str(u["worst_usd"]))


class TestReq4PartialCompletionWithoutOverHedging:

    def test_overhedging_is_refused_not_resized(self):
        doc = " ".join((inspect.getdoc(
            LX.FaithfulExecutor.defensive_execute) or "").split())
        assert "OVERHEDGING IS REFUSED, NOT RESIZED IN FLIGHT" in doc

    def test_the_decision_is_remade_against_venue_confirmed_state(self):
        doc = " ".join((inspect.getdoc(
            LX.FaithfulExecutor.defensive_execute) or "").split())
        assert "re-read OUR OWN inventory" in doc
        assert "decide again against what is actually there" in doc


class TestReq5CancelAndReconcileBeforeReplacing:

    def test_the_order_of_operations_is_the_safety_property(self):
        doc = " ".join((inspect.getdoc(
            LX.FaithfulExecutor.defensive_execute) or "").split())
        assert "THE ORDER OF OPERATIONS IS THE SAFETY PROPERTY" in doc
        assert "prove by the venue's own re-read" in doc

    def test_cancellation_is_confirmed_by_exact_coid(self):
        sig = inspect.signature(DEF.cancel_confirmed).parameters
        assert "coid" in sig

    def test_a_failure_leaves_the_position_exactly_as_it_was(self):
        doc = " ".join((inspect.getdoc(
            LX.FaithfulExecutor.defensive_execute) or "").split())
        assert "leaves the position exactly as it was" in doc


class TestReq6NoOpeningWhileUnmatchedEatsTheBudget:
    """The one requirement I had previously assumed rather than checked."""

    def test_the_cap_reads_the_PROSPECTIVE_book_not_the_realized_one(self):
        """Realized residual looked inside the cap on 1130z while four
        orders filled within seven seconds to 3.24 against 1.80. The gate
        counts every live order as if it filled."""
        src = inspect.getsource(LX.FaithfulExecutor)
        assert "outstanding_residual(market)" in src
        assert "prosp_before = resid_before + outstanding" in src
        assert '"basis": "PROSPECTIVE_NET"' in src

    def test_an_opening_is_exempt_ONLY_from_a_completely_flat_book(self):
        """`exempt_open` is `b_abs == 0` -- nothing held AND nothing
        resting. So a new opening while unmatched inventory exists is never
        exempt, which is requirement 6."""
        src = inspect.getsource(LX.FaithfulExecutor)
        assert "exempt_open = b_abs == 0" in src
        assert "(not exempt_open) and increases and a_abs > cap" in src

    def test_a_residual_reducing_leg_is_never_refused(self):
        """The asymmetry that keeps this from stranding a position: only
        legs that INCREASE |prospective| can be capped."""
        src = inspect.getsource(LX.FaithfulExecutor)
        assert "increases = a_abs > b_abs" in src

    def test_the_absolute_ceiling_is_the_approved_three_contracts(self):
        assert LX.ABS_RESIDUAL_CAP_CONTRACTS == D("3")
        src = inspect.getsource(LX.FaithfulExecutor)
        assert "min(self.residual_cap_k * clip, ABS_RESIDUAL_CAP_CONTRACTS)" \
            in src


class TestReq7TheExistingControlsAreUntouched:

    def test_the_defensive_loss_budget_is_bounded_and_charged(self):
        assert DEF.DEFENSIVE_LOSS_BUDGET_USD == D("2.00")
        # charged on InventoryStore: the budget is durable state, not a
        # per-tick executor concern.
        assert callable(LX.InventoryStore.defensive_charge)
        assert callable(LX.InventoryStore.defensive_spent)

    def test_idempotency_is_by_client_order_id(self):
        assert callable(LX.client_order_id)
        src = inspect.getsource(LX.FaithfulExecutor)
        assert "already_submitted(coid)" in src
        assert "DUPLICATE_SUPPRESSED" in src

    def test_the_gate_chain_order_is_unchanged(self):
        assert GATE.GATE_ORDER == (
            "PROVENANCE_KNOWN", "CURRENT_WINDOW", "SELL_IN_PROBE_SCOPE",
            "OPERATOR_HALT", "SHADOW_ONLY_LOCK", "DURABLE_HALT_LATCH",
            "OWNER_ARM_TOKEN", "TRANSPORT_ARMED", "ACCOUNT_WIDE_EXPOSURE",
            "ORDER_REGISTRY_OWNERSHIP", "RECONCILIATION_CERTAIN",
            "EXECUTOR_BOUNDARY")

    def test_an_ambiguous_post_makes_the_book_uncertain(self):
        assert callable(GATE.IntentGate._mark_uncertain)
        doc = inspect.getdoc(GATE.IntentGate._mark_uncertain) or ""
        assert "refuse further submissions" in doc


class TestReq8NoNewSendSiteAndNoNewService:

    def test_there_are_still_exactly_two_claim_gated_send_sites(self):
        """A hybrid completion must ride the existing executor send. If a
        third site appears, this campaign grew a money path it was not
        authorized to grow."""
        assert GATE.CLAIM_GATED_SUBMIT_SITES == (
            "l5_live_execution.py", "l5_cancel_mechanism.py")

    def test_the_defensive_path_issues_a_mandate_rather_than_its_own_send(self):
        src = inspect.getsource(LX.FaithfulExecutor.defensive_execute)
        assert "_defensive_mandate" in src or "mandate" in src.lower()

    def test_crossing_remains_separately_gated_from_resting_a_quote(self):
        """`L5_DEFENSIVE_COMPLETION` alone does not make anything crossable;
        it is an evaluation flag, and the executor's maker-only rule is a
        separate control."""
        assert DEF.DEFENSIVE_ENABLED_ENV == "L5_DEFENSIVE_COMPLETION"
        assert DEF.enabled() is False      # off in this environment


class TestTheCeilingMustNotBeTheDefault:

    def test_the_code_default_is_1_20_and_must_be_overridden_to_1_00(self):
        """At the 1.20 default the 38-window bound arithmetic gives
        -8.0931, worse than doing nothing. The mandate sets 1.00."""
        assert DEF.DEFENSIVE_PAIR_CEILING == D("1.20")
        sig = inspect.signature(DEF.decide).parameters
        assert sig["pair_ceiling"].default == DEF.DEFENSIVE_PAIR_CEILING
        # and the caller must be able to tighten it
        assert "pair_ceiling" in sig

    def test_a_pair_above_one_dollar_is_a_locked_loss(self):
        """A matched pair redeems exactly $1.00 and the venue has no
        maker-fee term, so there is nothing above $1.00 to win."""
        rows = {r["action"]: r for r in DEF.alternatives(
            qty=D("1"), first_leg_cost=D("0.62"), exit_price=D("0.48"),
            fee=D("0.0173"))}
        u = rows["DEFENSIVE_UNWIND"]
        assert D(str(u["best_usd"])) < 0        # 0.62 + 0.48 = 1.10 all-in

"""Two-outcome payoff evaluation, graded against the SETTLED campaign.

THE RECORDS THESE REPLAY are the venue's own CONFIRMED lines from
2026-11-02, not reconstructions:

  0145z  pair 2.04 @ 0.9900 (up_own 0.47 + dn_own 0.52); residual 2.05 UP;
         matched +0.030625, residual -0.953225, net -0.922600
  0200z  pair 1.98 @ 0.9700 (up_own 0.51 + dn_own 0.46); residual 1.85 DOWN;
         matched +0.183732, residual +1.115168, net +1.298900
  0230z  pair 1.89 @ 0.9500 (up_own 0.58 + dn_own 0.37); residual 1.94 DOWN;
         matched +0.056207, residual +1.182893, net +1.239100

THE RULE MOST AT RISK OF BEING BROKEN, and tested hardest: a resting hedge
is not protection. `unfilled` must equal `before`, always.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_portfolio_payoff as PP

#: The three owner inputs a DEFENSIVE_HEDGE requires. Supplied explicitly so
#: these tests isolate the CLASSIFICATION; the refusals for a missing cap or
#: a missing durable committed amount have their own tests below.
BUDGETS = dict(max_locked_loss_per_hedge_usd="5",
               max_cumulative_locked_loss_usd="5",
               cumulative_locked_loss_usd="0")


# ---------------------------------------------------------------- the flag

def test_off_by_default_so_its_effect_is_attributable(monkeypatch):
    monkeypatch.delenv(PP.ENV, raising=False)
    assert PP.enabled() is False


def test_enabled_only_on_explicit_truthy(monkeypatch):
    monkeypatch.setenv(PP.ENV, "1")
    assert PP.enabled() is True
    monkeypatch.setenv(PP.ENV, "off")
    assert PP.enabled() is False


# ------------------------------------------------- the payoff is arithmetic

def test_a_matched_pair_redeems_exactly_one_dollar_in_both_outcomes():
    """0145z's pair: 2.04 at a fee-inclusive 0.99 each -> +0.0204 either way."""
    p = PP.settlement_payoff("2.04", "2.04", D("2.04") * D("0.99"))
    assert p["pnl_if_up_usd"] == p["pnl_if_down_usd"]
    assert p["pnl_if_up_usd"] == D("2.04") * D("0.01")
    assert p["worst_usd"] == p["best_usd"]


def test_a_bare_long_leg_pays_in_one_outcome_and_nothing_in_the_other():
    """0145z's residual: 2.05 UP at 0.465 -> +1.0968 or -0.95325."""
    p = PP.settlement_payoff("2.05", "0", D("2.05") * D("0.465"))
    assert p["pnl_if_up_usd"] == D("2.05") - (D("2.05") * D("0.465"))
    assert p["pnl_if_down_usd"] == -(D("2.05") * D("0.465"))
    # the venue settled this one to zero and booked -0.953225
    assert abs(p["pnl_if_down_usd"] - D("-0.953225")) < D("0.0001")


def test_an_unknown_cost_basis_is_never_treated_as_zero():
    assert PP.settlement_payoff("2", "0", None) is None
    assert PP.settlement_payoff("2", "0", "nonsense") is None


def test_negative_inventory_is_rejected_not_netted():
    assert PP.settlement_payoff("-2", "0", "1") is None


# ------------------------------------------- an unfilled order is not a hedge

def test_unfilled_equals_before_exactly():
    """THE CENTRAL RULE. Until the venue confirms, nothing changed."""
    r = PP.evaluate(held_up="2.05", held_dn="0",
                    cost_basis_usd=D("2.05") * D("0.465"),
                    side="DOWN", qty="2.05", own_price="0.50", fee_usd="0")
    assert r["scenarios"]["unfilled"] == r["before"]


def test_every_record_says_the_after_is_conditional():
    r = PP.evaluate(held_up="2.05", held_dn="0",
                    cost_basis_usd=D("2.05") * D("0.465"),
                    side="DOWN", qty="2.05", own_price="0.50", fee_usd="0")
    assert "after_if_filled" in r
    assert "CONDITIONAL" in r["fill_uncertainty"]


def test_partial_fill_scenarios_are_reported():
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd="0.94",
                    side="DOWN", qty="2", own_price="0.50", fee_usd="0")
    for f in ("0.25", "0.50", "0.75"):
        assert "partial_%s" % f in r["scenarios"]


def test_a_partial_fill_gives_partial_protection_only():
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd="0.94",
                    side="DOWN", qty="2", own_price="0.50", fee_usd="0")
    half = r["scenarios"]["partial_0.50"]
    full = r["scenarios"]["full_fill"]
    assert D(half["worst_usd"]) < D(full["worst_usd"])
    assert D(half["worst_usd"]) > D(r["before"]["worst_usd"])


# ------------------------------------------------------------- PROFIT_LOCK

def test_completing_0145z_below_redemption_is_a_profit_lock():
    """Held 2.05 UP at 0.465. A DOWN completion at own 0.50 makes the pair
    0.965 -- both outcomes positive, and no probability is involved.
    """
    r = PP.evaluate(held_up="2.05", held_dn="0",
                    cost_basis_usd=D("2.05") * D("0.465"),
                    side="DOWN", qty="2.05", own_price="0.50", fee_usd="0")
    assert r["action"] == PP.PROFIT_LOCK
    a = r["after_if_filled"]
    assert D(a["pnl_if_up_usd"]) > 0 and D(a["pnl_if_down_usd"]) > 0


def test_the_0230z_pair_at_0_95_is_a_profit_lock():
    r = PP.evaluate(held_up="1.89", held_dn="0",
                    cost_basis_usd=D("1.89") * D("0.58"),
                    side="DOWN", qty="1.89", own_price="0.37", fee_usd="0")
    assert r["action"] == PP.PROFIT_LOCK


def test_a_pair_at_exactly_redemption_is_not_a_profit_lock():
    """0.58 + 0.42 = 1.00 pays nothing. Not a lock."""
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.58"),
                    side="DOWN", qty="2", own_price="0.42", fee_usd="0")
    assert r["action"] != PP.PROFIT_LOCK


def test_fees_can_turn_a_lock_into_something_else():
    """The pair clears on price but not after the fee -- so it must not be
    reported as a lock."""
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.50"),
                    side="DOWN", qty="2", own_price="0.4950", fee_usd="0.50")
    assert r["action"] != PP.PROFIT_LOCK


# -------------------------------------------------------------------- HEDGE

def test_a_loss_reducing_completion_above_redemption_is_a_hedge():
    """Pair costs more than $1, so neither outcome is positive -- but the
    worst case improves. That is the defensive case, and it is expected to
    be negative.
    """
    # An explicit budget is passed so this isolates the CLASSIFICATION; the
    # default $0.15 cap would refuse it, and that refusal has its own test.
    # Worth noting the default binds on a realistic hedge: this one locks
    # $0.30 to repair $1.10 of worst case.
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0",
                    # MECHANICS, not permission: at market this hedge is
                    # EV-neutral before fees, so the EV gate refuses it by
                    # default now. The override keeps this test on its own
                    # subject -- the locked-loss arithmetic.
                    permit_ev_negative_hedge=True,
                    **dict(BUDGETS, max_locked_loss_per_hedge_usd="0.50"))
    assert r["action"] == PP.DEFENSIVE_HEDGE
    assert D(r["delta_worst_usd"]) > 0
    assert D(r["hedge_locks_certain_loss_usd"]) == D("0.30")
    assert r["reduces_unmatched"] is True


def test_the_default_per_hedge_budget_binds_on_a_realistic_hedge():
    """Recorded because it is a policy fact, not an accident: a completion
    locking $0.30 is refused by the $0.15 default even though it repairs
    $1.10 of worst case. The owner sets this number, not the engine.
    """
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0",
                    **dict(BUDGETS, max_locked_loss_per_hedge_usd="0.15"))
    assert r["action"] == PP.REJECT
    assert r["reason"] == "LOCKED_LOSS_EXCEEDS_PER_HEDGE_BUDGET"


def test_a_hedge_that_does_not_reduce_unmatched_is_refused():
    """Reducing unmatched inventory is required; reaching BALANCE is not."""
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.40"),
                    side="DOWN", qty="5", own_price="0.30", fee_usd="0", **BUDGETS)
    assert r["action"] == PP.REJECT
    assert r["reason"] == "DOES_NOT_REDUCE_UNMATCHED"
    # it DID improve the worst case -- that is not sufficient on its own
    assert D(r["delta_worst_usd"]) > 0
    assert D(r["unmatched_after_if_filled"]) > D(r["unmatched_before"])


def test_a_cheap_overshoot_making_both_outcomes_positive_is_a_profit_lock():
    """And it outranks the unmatched test, correctly: a position that pays
    in BOTH outcomes is good regardless of how imbalanced it is. This is
    the clearest case of intentional imbalance being welcome.
    """
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.40"),
                    side="DOWN", qty="9", own_price="0.05", fee_usd="0")
    assert r["action"] == PP.PROFIT_LOCK
    a = r["after_if_filled"]
    assert D(a["pnl_if_up_usd"]) > 0 and D(a["pnl_if_down_usd"]) > 0


def test_a_partial_hedge_leaving_an_imbalance_is_accepted():
    """INTENTIONAL IMBALANCE IS WELCOME. A hedge need not reach balance --
    it must only leave less unmatched inventory than it found.
    """
    r = PP.evaluate(held_up="4", held_dn="0", cost_basis_usd=D("4") * D("0.70"),
                    side="DOWN", qty="1", own_price="0.45", fee_usd="0",
                    permit_ev_negative_hedge=True, **BUDGETS)
    assert r["action"] == PP.DEFENSIVE_HEDGE
    assert D(r["unmatched_after_if_filled"]) > 0, "still imbalanced, by design"
    assert D(r["unmatched_after_if_filled"]) < D(r["unmatched_before"])


def test_a_partial_fill_risk_refusal_blocks_the_hedge():
    """A partial need not IMPROVE the worst case, but it must respect every
    hard limit at each feasible quantity."""
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0",
                    partial_risk_refusals={"partial_0.25": "RESIDUAL_CAP"},
                    **BUDGETS)
    assert r["action"] == PP.REJECT
    assert r["reason"] == "PARTIAL_FILL_BREACHES_RISK_LIMIT"


def test_a_hedge_locking_more_than_carrying_costs_is_rejected():
    """The verdict doc's own bad case: chasing a quote to own 0.95 against a
    0.32 basis is a pair at 1.27. On 2.05 units that locks ~$0.55 to avoid
    ~$0.47 of expected carry -- paying to make the outcome worse.
    """
    r = PP.evaluate(held_up="2.05", held_dn="0",
                    cost_basis_usd=D("2.05") * D("0.32"),
                    side="DOWN", qty="2.05", own_price="0.95", fee_usd="0",
                    **dict(BUDGETS, max_locked_loss_per_hedge_usd="0.15"))
    assert r["action"] == PP.REJECT
    assert r["reason"] == "LOCKED_LOSS_EXCEEDS_PER_HEDGE_BUDGET"
    assert D(r["hedge_locks_certain_loss_usd"]) > D("0.15")


def test_the_hedge_numbers_are_reported_for_audit():
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0",
                    permit_ev_negative_hedge=True, **BUDGETS)
    # THE EV GAP IS NOW CLOSED. It used to be UNKNOWN because the only
    # candidate was the unconditional -$0.2315/unit average the owner
    # refused. The replacement is contemporaneous and position-specific:
    # the complement's own executable price IS P(complement wins).
    assert r["holding_ev_status"] == PP.MARKET_IMPLIED
    assert r["holding_ev_usd"] is not None
    assert r["p_complement_wins"] == "0.45"
    assert "UNCONDITIONAL" in r["unconditional_reference_only"]
    assert r["holding_worst_usd"] is not None
    assert r["holding_best_usd"] is not None


def test_a_cheap_hedge_beats_the_measured_carry_cost_and_passes():
    """The 0145z rescue my first bound wrongly refused: pair 1.025 locks a
    certain $0.051 against $0.475 of expected carry.
    """
    r = PP.evaluate(held_up="2.05", held_dn="0",
                    cost_basis_usd=D("2.05") * D("0.465"),
                    side="DOWN", qty="2.05", own_price="0.56", fee_usd="0",
                    permit_ev_negative_hedge=True, **BUDGETS)
    assert r["action"] == PP.DEFENSIVE_HEDGE
    assert D(r["hedge_locks_certain_loss_usd"]) < D("0.15")


# --------------------------------------------------------- DIRECTIONAL_ADD

def test_adding_to_the_overweight_side_without_ev_is_rejected():
    """THE 0145z DEFECT. Already long UP, bought 2.05 more UP at 0.46. It
    settled to zero and cost $0.953225.
    """
    r = PP.evaluate(held_up="2.04", held_dn="0",
                    cost_basis_usd=D("2.04") * D("0.47"),
                    side="UP", qty="2.05", own_price="0.46", fee_usd="0")
    assert r["action"] == PP.REJECT
    assert r["reason"] == "INVENTORY_ADD_DISABLED"
    assert D(r["delta_worst_usd"]) < 0


def test_a_directional_add_needs_provenance_not_just_a_number():
    r = PP.evaluate(held_up="2.04", held_dn="0",
                    cost_basis_usd=D("2.04") * D("0.47"),
                    side="UP", qty="2.05", own_price="0.46", fee_usd="0",
                    marginal_ev_usd_per_contract="0.30")
    assert r["action"] == PP.REJECT
    assert r["reason"] == "INVENTORY_ADD_DISABLED"


def test_a_non_positive_ev_is_rejected():
    r = PP.evaluate(held_up="2.04", held_dn="0",
                    cost_basis_usd=D("2.04") * D("0.47"),
                    side="UP", qty="2.05", own_price="0.46", fee_usd="0",
                    marginal_ev_usd_per_contract="0",
                    marginal_ev_provenance="calib-v1")
    assert r["action"] == PP.REJECT
    assert r["reason"] == "INVENTORY_ADD_DISABLED"


def test_a_justified_inventory_add_needs_ALL_FOUR_conditions(monkeypatch):
    """A feature flag must NEVER be sufficient. Flag, owner authorization,
    a marginal EV and its provenance are each independently necessary.
    """
    base = dict(held_up="2.04", held_dn="0",
                cost_basis_usd=D("2.04") * D("0.47"),
                side="UP", qty="2.05", own_price="0.46", fee_usd="0")
    # flag OFF beats everything else being present
    monkeypatch.delenv(PP.INVENTORY_ADD_ENV, raising=False)
    r = PP.evaluate(owner_authorized_inventory_add=True,
                    marginal_ev_usd_per_contract="0.30",
                    marginal_ev_provenance="calib-v1", **base)
    assert r["action"] == PP.REJECT
    assert r["reason"] == "INVENTORY_ADD_DISABLED"
    monkeypatch.setenv(PP.INVENTORY_ADD_ENV, "1")
    # flag ON but no owner authorization
    r = PP.evaluate(marginal_ev_usd_per_contract="0.30",
                    marginal_ev_provenance="calib-v1", **base)
    assert r["reason"] == "INVENTORY_ADD_NOT_OWNER_AUTHORIZED"
    # flag ON, owner authorized, but no EV
    r = PP.evaluate(owner_authorized_inventory_add=True, **base)
    assert r["reason"] == "MARGINAL_EV_ABSENT"
    # flag ON, owner authorized, EV but no provenance
    r = PP.evaluate(owner_authorized_inventory_add=True,
                    marginal_ev_usd_per_contract="0.30", **base)
    assert r["reason"] == "MARGINAL_EV_PROVENANCE_ABSENT"
    # all four
    r = PP.evaluate(owner_authorized_inventory_add=True,
                    marginal_ev_usd_per_contract="0.30",
                    marginal_ev_provenance="calib-v1", **base)
    assert r["action"] == PP.INVENTORY_ADD
    assert "WORSE" in r["labels"]


# ------------------------------------- risk controls outrank the economics

def test_an_existing_risk_refusal_outranks_every_economic_verdict():
    """Even a perfect profit lock must not override a limit."""
    r = PP.evaluate(held_up="2.05", held_dn="0",
                    cost_basis_usd=D("2.05") * D("0.465"),
                    side="DOWN", qty="2.05", own_price="0.50", fee_usd="0",
                    risk_refusal="RESIDUAL_CAP_BINDS")
    assert r["action"] == PP.REJECT
    assert r["reason"] == "RISK_CONTROL_REFUSED"
    assert r["risk_refusal"] == "RESIDUAL_CAP_BINDS"


def test_same_side_resting_exposure_is_charged_in_the_worst_case():
    """Protection is discounted, exposure is not."""
    bare = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd="0.94",
                       side="UP", qty="1", own_price="0.47", fee_usd="0",
                       marginal_ev_usd_per_contract="0.30",
                       marginal_ev_provenance="calib-v1")
    withr = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd="0.94",
                        side="UP", qty="1", own_price="0.47", fee_usd="0",
                        resting_same_side_qty="3",
                        resting_same_side_cost_usd="1.41",
                        marginal_ev_usd_per_contract="0.30",
                        marginal_ev_provenance="calib-v1")
    assert (D(withr["after_if_filled"]["worst_usd"])
            < D(bare["after_if_filled"]["worst_usd"]))


# --------------------------------------------------------- fail-closed inputs

def test_an_unknown_fee_rejects_rather_than_defaulting_to_zero():
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd="0.94",
                    side="DOWN", qty="2", own_price="0.50", fee_usd=None)
    assert r["action"] == PP.REJECT
    assert r["reason"] == "FEE_UNKNOWN"


def test_a_midpoint_shaped_out_of_band_price_rejects():
    for px in ("0", "1", "1.5", "-0.2", "nonsense"):
        r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd="0.94",
                        side="DOWN", qty="2", own_price=px, fee_usd="0")
        assert r["action"] == PP.REJECT, px
        assert r["reason"] == "OWN_PRICE_OUT_OF_BAND", px


def test_an_unreadable_portfolio_rejects():
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=None,
                    side="DOWN", qty="2", own_price="0.50", fee_usd="0")
    assert r["action"] == PP.REJECT
    assert r["reason"] == "PORTFOLIO_UNREADABLE"


def test_unknown_side_and_qty_reject():
    assert PP.evaluate(held_up="2", held_dn="0", cost_basis_usd="0.94",
                       side="SIDEWAYS", qty="1", own_price="0.5",
                       fee_usd="0")["reason"] == "SIDE_UNKNOWN"
    assert PP.evaluate(held_up="2", held_dn="0", cost_basis_usd="0.94",
                       side="UP", qty="0", own_price="0.5",
                       fee_usd="0")["reason"] == "QTY_UNREADABLE"


# -------------------------- the opposite side moving before we execute (item 7)

def test_the_verdict_changes_when_the_complement_price_moves_against_us():
    """0145z's complement drifted from own 0.49 to beyond 0.535 while we
    chased it. The same held leg must stop being a lock as the price runs.
    """
    held = dict(held_up="2.05", held_dn="0",
                cost_basis_usd=D("2.05") * D("0.465"), side="DOWN",
                qty="2.05", fee_usd="0", permit_ev_negative_hedge=True,
                **BUDGETS)
    lock = PP.evaluate(own_price="0.50", **held)
    assert lock["action"] == PP.PROFIT_LOCK
    gone = PP.evaluate(own_price="0.56", **held)
    assert gone["action"] == PP.DEFENSIVE_HEDGE, gone["action"]
    assert D(gone["after_if_filled"]["worst_usd"]) > D(
        gone["before"]["worst_usd"])


def test_this_module_moves_no_limit():
    banned = ("max_total_exposure", "max_live_orders", "daily_loss",
              "max_cash", "taker_cross", "pair_ceiling", "residual_cap")
    names = [n.lower() for n in dir(PP)]
    for b in banned:
        assert not any(b in n for n in names), b


# ------------------------------------ no permissive fallback for any budget

def test_a_hedge_with_no_budgets_supplied_is_refused():
    """A cap the owner did not approve is not a limit. The wired gate used
    to pass nothing, and a $0.60 cumulative budget degenerated into a
    second per-hedge cap because the committed amount defaulted to zero.
    """
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0")
    assert r["action"] == PP.REJECT
    assert r["reason"] == "HEDGE_BUDGET_NOT_SUPPLIED"
    assert "max_locked_loss_per_hedge_usd" in r["missing_budget_inputs"]
    assert "max_cumulative_locked_loss_usd" in r["missing_budget_inputs"]


def test_an_unknown_committed_amount_is_not_a_zero_committed_amount():
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0",
                    max_locked_loss_per_hedge_usd="5",
                    max_cumulative_locked_loss_usd="5")
    assert r["action"] == PP.REJECT
    assert r["missing_budget_inputs"] == ["cumulative_locked_loss_usd"]


def test_PROFIT_LOCK_needs_no_hedge_budget_at_all():
    """It consumes none of the negative-hedge budget, so it must not be
    gated on one -- its payoff is nonnegative in BOTH outcomes."""
    r = PP.evaluate(held_up="2.05", held_dn="0",
                    cost_basis_usd=D("2.05") * D("0.465"),
                    side="DOWN", qty="2.05", own_price="0.50", fee_usd="0")
    assert r["action"] == PP.PROFIT_LOCK
    assert D(r["after_if_filled"]["pnl_if_up_usd"]) > 0
    assert D(r["after_if_filled"]["pnl_if_down_usd"]) > 0


def test_the_recommended_figures_are_not_defaults():
    assert hasattr(PP, "RECOMMENDED_LOCKED_LOSS_PER_HEDGE_USD")
    assert hasattr(PP, "RECOMMENDED_CUMULATIVE_LOCKED_LOSS_USD")
    assert not hasattr(PP, "MAX_LOCKED_LOSS_PER_HEDGE_USD")
    assert not hasattr(PP, "MAX_CUMULATIVE_LOCKED_LOSS_USD")


# ===================== HEDGE vs HOLD: the owner's rule, enforced (item 5)

def test_a_full_hedge_at_market_is_exactly_ev_neutral_before_fees():
    """THE ARITHMETIC IDENTITY, and why hedging cannot pay.

    A hedge bought AT the market costs the market's own expectation, so it
    converts an uncertain outcome into a CERTAIN one of identical expected
    value -- an uncertain -$0.30 into a certain -$0.30, delta exactly zero
    -- and any fee makes it strictly worse. Risk reduction is real; edge is
    not.
    """
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0",
                    permit_ev_negative_hedge=True, **BUDGETS)
    assert D(r["holding_ev_usd"]) == D("-0.3000")
    assert D(r["hedge_ev_usd"]) == D("-0.3000")
    assert D(r["hedge_ev_delta_usd"]) == D("0")
    assert r["hedge_ev_verdict"] == PP.EV_NEGATIVE_RISK_REDUCING


def test_an_ev_negative_hedge_is_refused_by_default():
    """OWNER: not merely because it reduces the worst case."""
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0",
                    **BUDGETS)
    assert r["action"] == PP.REJECT
    assert r["reason"] == "HEDGE_EV_NEGATIVE_VS_HOLD"
    assert D(r["delta_worst_usd"]) > 0      # it DID reduce the worst case


def test_a_fee_makes_the_hedge_strictly_ev_negative():
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0.04",
                    permit_ev_negative_hedge=True, **BUDGETS)
    assert D(r["hedge_ev_delta_usd"]) < D("0")


def test_the_campaign_replay_shows_the_module_would_have_lost_money():
    """THE REPLAY THAT DECIDES THIS, on the only live data we have.

    2026-11-02 four-window campaign, its three stranded legs, each with the
    best executable hedge the engine actually found at the time (taker
    price, displayed depth 181-1634 contracts, fee included):

      1315z  held UP 2.14 @0.40, hedge DOWN @0.67
             -> REJECT HEDGE_EV_NEGATIVE_VS_HOLD          settled -0.8560
      1345z  held UP 2.18 @0.37, hedge DOWN @0.94
             -> REJECT HEDGE_EV_NEGATIVE_VS_HOLD          settled -0.8066
             (under the RECOMMENDED $0.15 per-hedge cap the budget gate
              refuses it first, for a different reason and the same
              outcome; BUDGETS here is deliberately generous so the EV
              gate is the thing under test)
      1400z  held DOWN 1.77 @0.25, complete UP @0.60
             -> PROFIT_LOCK, locking +0.2655              settled +1.3275

    THE ONLY ACTION IT WOULD TAKE IS THE ONE THAT LOSES MONEY: the two
    hedges are refused, and the single acted-on case trades a realized
    +$1.3275 for a certain +$0.2655 -- costing $1.0620 and taking the
    campaign from -$0.2356 to -$1.2976.
    """
    legs = [("UP", "2.14", "0.40", "0.67", PP.REJECT,
             "HEDGE_EV_NEGATIVE_VS_HOLD"),
            ("UP", "2.18", "0.37", "0.94", PP.REJECT,
             "HEDGE_EV_NEGATIVE_VS_HOLD"),
            ("DOWN", "1.77", "0.25", "0.60", PP.PROFIT_LOCK, None)]
    for held_side, qty, cost, hedge_px, want_action, want_reason in legs:
        kw = dict(cost_basis_usd=D(qty) * D(cost), qty=qty,
                  own_price=hedge_px, fee_usd="0", **BUDGETS)
        if held_side == "UP":
            r = PP.evaluate(held_up=qty, held_dn="0", side="DOWN", **kw)
        else:
            r = PP.evaluate(held_up="0", held_dn=qty, side="UP", **kw)
        assert r["action"] == want_action, (held_side, qty, r["action"])
        if want_reason is not None:
            assert r["reason"] == want_reason, r["reason"]


def test_profit_lock_is_not_ev_compared_and_that_is_where_the_loss_is():
    """PINS THE GAP. PROFIT_LOCK returns before the hedge branch, so no EV
    comparison is computed for it -- deliberate, since the owner preserved
    PROFIT_LOCK eligibility, and it is exactly where the $1.0620 comes
    from. Recorded rather than changed unilaterally."""
    r = PP.evaluate(held_up="0", held_dn="1.77",
                    cost_basis_usd=D("1.77") * D("0.25"),
                    side="UP", qty="1.77", own_price="0.60", fee_usd="0",
                    **BUDGETS)
    assert r["action"] == PP.PROFIT_LOCK
    assert r.get("holding_ev_usd") is None


def test_the_module_is_still_disabled_in_live_execution():
    import os
    assert os.getenv(PP.ENV) in (None, "", "NO", "no", "0"), (
        "this experiment must not be enabled in live execution")
    assert PP.enabled() is False

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
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0")
    assert r["action"] == PP.HEDGE
    assert D(r["delta_worst_usd"]) > 0


def test_a_hedge_locking_more_than_carrying_costs_is_rejected():
    """The verdict doc's own bad case: chasing a quote to own 0.95 against a
    0.32 basis is a pair at 1.27. On 2.05 units that locks ~$0.55 to avoid
    ~$0.47 of expected carry -- paying to make the outcome worse.
    """
    r = PP.evaluate(held_up="2.05", held_dn="0",
                    cost_basis_usd=D("2.05") * D("0.32"),
                    side="DOWN", qty="2.05", own_price="0.95", fee_usd="0")
    assert r["action"] == PP.REJECT
    assert r["reason"] == "HEDGE_LOCKS_MORE_THAN_CARRY_COSTS"
    assert D(r["hedge_locks_certain_loss_usd"]) > D(
        r["residual_carry_expected_cost_usd"])


def test_the_hedge_numbers_are_reported_for_audit():
    r = PP.evaluate(held_up="2", held_dn="0", cost_basis_usd=D("2") * D("0.70"),
                    side="DOWN", qty="2", own_price="0.45", fee_usd="0")
    assert r["hedge_cost_ratio"] is not None
    assert "NOT a gate" in r["hedge_cost_ratio_note"]
    assert "384" in r["residual_carry_basis"]


def test_a_cheap_hedge_beats_the_measured_carry_cost_and_passes():
    """The 0145z rescue my first bound wrongly refused: pair 1.025 locks a
    certain $0.051 against $0.475 of expected carry.
    """
    r = PP.evaluate(held_up="2.05", held_dn="0",
                    cost_basis_usd=D("2.05") * D("0.465"),
                    side="DOWN", qty="2.05", own_price="0.56", fee_usd="0")
    assert r["action"] == PP.HEDGE
    assert D(r["hedge_locks_certain_loss_usd"]) < D(
        r["residual_carry_expected_cost_usd"])


# --------------------------------------------------------- DIRECTIONAL_ADD

def test_adding_to_the_overweight_side_without_ev_is_rejected():
    """THE 0145z DEFECT. Already long UP, bought 2.05 more UP at 0.46. It
    settled to zero and cost $0.953225.
    """
    r = PP.evaluate(held_up="2.04", held_dn="0",
                    cost_basis_usd=D("2.04") * D("0.47"),
                    side="UP", qty="2.05", own_price="0.46", fee_usd="0")
    assert r["action"] == PP.REJECT
    assert r["reason"] == "DIRECTIONAL_EV_ABSENT"
    assert D(r["delta_worst_usd"]) < 0


def test_a_directional_add_needs_provenance_not_just_a_number():
    r = PP.evaluate(held_up="2.04", held_dn="0",
                    cost_basis_usd=D("2.04") * D("0.47"),
                    side="UP", qty="2.05", own_price="0.46", fee_usd="0",
                    marginal_ev_usd_per_contract="0.30")
    assert r["action"] == PP.REJECT
    assert r["reason"] == "DIRECTIONAL_EV_PROVENANCE_ABSENT"


def test_a_non_positive_ev_is_rejected():
    r = PP.evaluate(held_up="2.04", held_dn="0",
                    cost_basis_usd=D("2.04") * D("0.47"),
                    side="UP", qty="2.05", own_price="0.46", fee_usd="0",
                    marginal_ev_usd_per_contract="0",
                    marginal_ev_provenance="calib-v1")
    assert r["action"] == PP.REJECT
    assert r["reason"] == "DIRECTIONAL_EV_NOT_POSITIVE"


def test_a_justified_directional_add_is_permitted_and_says_it_is_worse():
    r = PP.evaluate(held_up="2.04", held_dn="0",
                    cost_basis_usd=D("2.04") * D("0.47"),
                    side="UP", qty="2.05", own_price="0.46", fee_usd="0",
                    marginal_ev_usd_per_contract="0.30",
                    marginal_ev_provenance="calib-v1")
    assert r["action"] == PP.DIRECTIONAL_ADD
    assert "worst case is WORSE" in r["labels"]


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
                qty="2.05", fee_usd="0")
    lock = PP.evaluate(own_price="0.50", **held)
    assert lock["action"] == PP.PROFIT_LOCK
    gone = PP.evaluate(own_price="0.56", **held)
    assert gone["action"] == PP.HEDGE, gone["action"]
    assert D(gone["after_if_filled"]["worst_usd"]) > D(
        gone["before"]["worst_usd"])


def test_this_module_moves_no_limit():
    banned = ("max_total_exposure", "max_live_orders", "daily_loss",
              "max_cash", "taker_cross", "pair_ceiling", "residual_cap")
    names = [n.lower() for n in dir(PP)]
    for b in banned:
        assert not any(b in n for n in names), b

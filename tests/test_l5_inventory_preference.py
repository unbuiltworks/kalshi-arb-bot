"""Inventory preference must favour completion WITHOUT stopping the market.

THE EVIDENCE IT ANSWERS. The settled 2026-11-02 campaign acquired a surplus
leg in three of four windows and the unmatched remainder swung -$0.953225 to
+$1.191300 per window, against +$0.270564 of matched P&L across all four.

THE TWO FAILURE MODES THESE GUARD. A gate that refuses complementary orders
would strand every leg; a gate that refuses opening legs would stop trading
altogether. Both are tested explicitly, because either would be worse than
having no gate at all.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_inventory_preference as IP


# ------------------------------------------------------- the flag is its own

def test_disabled_by_default_so_its_effect_is_attributable(monkeypatch):
    monkeypatch.delenv(IP.ENV, raising=False)
    assert IP.enabled() is False


def test_enabled_only_by_explicit_truthy_values(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(IP.ENV, v)
        assert IP.enabled() is True, v
    for v in ("0", "false", "no", "off", "", "maybe"):
        monkeypatch.setenv(IP.ENV, v)
        assert IP.enabled() is False, v


# --------------------------------------------- direction counts POTENTIAL fills

def test_potential_net_counts_both_sides_resting_orders():
    """The convention that makes a complementary order read as a reduction.
    The residual cap deliberately does the opposite, for risk; see the
    module docstring.
    """
    assert IP.potential_net(2, 0, 0, 0) == D(2)
    assert IP.potential_net(2, 0, 0, 2) == D(0)      # resting DOWN hedges
    assert IP.potential_net(0, 2, 2, 0) == D(0)
    assert IP.potential_net(2, 0, 1, 0) == D(3)      # resting UP adds


def test_unreadable_inventory_is_unknown_not_balanced():
    assert IP.potential_net("x", 0) is None
    assert IP.potential_net(0, None) is None


# --------------------------------------- complementary execution stays open

def test_a_complementary_order_is_always_permitted():
    """0145z held 2.05 UP unmatched. A DOWN completion must pass."""
    r = IP.decide(held_up="2.05", held_dn="0", side="DOWN", qty="2.05")
    assert r["act"] is True
    assert r["class"] == "REDUCING"
    assert D(r["abs_before"]) == D("2.05")
    assert D(r["abs_after"]) == D("0")


def test_a_partial_complement_still_reduces_and_passes():
    r = IP.decide(held_up="3", held_dn="0", side="DOWN", qty="1")
    assert r["act"] is True
    assert r["class"] == "REDUCING"


def test_no_expected_value_evidence_is_needed_to_reduce():
    """Completion must never be gated on an EV estimate we cannot produce."""
    r = IP.decide(held_up="2", held_dn="0", side="DOWN", qty="2",
                  ev_usd_per_contract=None, ev_provenance=None)
    assert r["act"] is True


# ----------------------------------------- the market is never simply stopped

def test_an_opening_leg_from_balance_is_permitted_on_either_side():
    """From flat, every first leg increases |exposure|. Charging it the EV
    bar would refuse all openings and halt trading -- the one thing this
    gate must not do.
    """
    for side in ("UP", "DOWN"):
        r = IP.decide(held_up="0", held_dn="0", side=side, qty="2")
        assert r["act"] is True, side
        assert r["class"] == "OPENING", side


def test_a_fresh_cycle_opens_immediately_after_rebalancing():
    """Repeated pairing cycles: once matched, the next opening is allowed
    with no cooldown and no EV argument.
    """
    r = IP.decide(held_up="2", held_dn="2", side="UP", qty="2")
    assert r["act"] is True
    assert r["class"] == "OPENING"


def test_balance_via_resting_orders_also_counts_as_balanced():
    r = IP.decide(held_up="2", held_dn="0", resting_dn="2",
                  side="UP", qty="1")
    assert r["class"] == "OPENING"
    assert r["act"] is True


# ------------------------------------------ overweight additions must argue

def test_the_0145z_surplus_leg_is_refused_without_an_ev_case():
    """THE ACTUAL DEFECT. 0145z already held UP with DOWN unmatched, then
    bought another 2.05 UP at own 0.46. That leg settled to zero and cost
    $0.953225.
    """
    r = IP.decide(held_up="2.04", held_dn="0", resting_dn="2.04",
                  side="UP", qty="2.05")
    # resting DOWN 2.04 balances held UP 2.04, so this is an OPENING of a
    # second cycle rather than an accumulation -- and that is the honest
    # reading of the book at that instant.
    assert r["class"] == "OPENING"
    assert r["act"] is True


def test_an_addition_with_nothing_resting_opposite_is_an_increase():
    r = IP.decide(held_up="2.04", held_dn="0", side="UP", qty="2.05")
    assert r["class"] == "INCREASING"
    assert r["act"] is False
    assert r["reason"] == "EV_EVIDENCE_ABSENT"


def test_absent_ev_evidence_is_never_read_as_positive():
    r = IP.decide(held_up="2", held_dn="0", side="UP", qty="1")
    assert r["act"] is False
    assert r["reason"] == "EV_EVIDENCE_ABSENT"
    assert "not positive expected value" in r["labels"]


def test_an_ev_without_provenance_is_not_defensible():
    r = IP.decide(held_up="2", held_dn="0", side="UP", qty="1",
                  ev_usd_per_contract="0.50")
    assert r["act"] is False
    assert r["reason"] == "EV_PROVENANCE_ABSENT"


def test_a_merely_positive_ev_does_not_clear_the_measured_residual_cost():
    """+$0.05 is positive and still loses: the exposure it creates costs
    $0.2315 per unit by measurement.
    """
    r = IP.decide(held_up="2", held_dn="0", side="UP", qty="1",
                  ev_usd_per_contract="0.05",
                  ev_provenance="calibrated-fill-model-v3")
    assert r["act"] is False
    assert r["reason"] == "EV_BELOW_THRESHOLD"
    assert r["ev_threshold_usd"] == "0.2315"


def test_the_threshold_boundary_is_not_cleared_by_equality():
    r = IP.decide(held_up="2", held_dn="0", side="UP", qty="1",
                  ev_usd_per_contract="0.2315",
                  ev_provenance="calibrated-fill-model-v3")
    assert r["act"] is False
    assert r["reason"] == "EV_BELOW_THRESHOLD"


def test_a_defensible_ev_above_the_measured_cost_is_permitted():
    r = IP.decide(held_up="2", held_dn="0", side="UP", qty="1",
                  ev_usd_per_contract="0.40",
                  ev_provenance="calibrated-fill-model-v3")
    assert r["act"] is True
    assert r["class_detail"] == "INCREASING_EV_JUSTIFIED"
    assert r["ev_provenance"] == "calibrated-fill-model-v3"


def test_the_threshold_carries_its_basis_so_it_can_be_audited():
    r = IP.decide(held_up="2", held_dn="0", side="UP", qty="1")
    assert "384 windows" in r["ev_threshold_basis"]


# ------------------------------------------------- overshoot and edge cases

def test_an_overshooting_complement_that_worsens_imbalance_must_argue():
    """Reducing 2 UP with a 9 DOWN order leaves us 7 DOWN-heavy. That is
    not a completion, it is a reversal, and it is charged accordingly.
    """
    r = IP.decide(held_up="2", held_dn="0", side="DOWN", qty="9")
    assert r["class"] == "INCREASING"
    assert r["act"] is False


def test_a_perfect_flip_is_neutral_and_not_charged():
    r = IP.decide(held_up="2", held_dn="0", side="DOWN", qty="4")
    assert r["class"] == "NEUTRAL"
    assert r["act"] is True


def test_an_unknown_side_fails_closed():
    r = IP.decide(held_up="2", held_dn="0", side="SIDEWAYS", qty="1")
    assert r["act"] is False
    assert r["reason"] == "SIDE_UNKNOWN"


def test_a_zero_or_negative_qty_fails_closed():
    for q in ("0", "-1", "nonsense"):
        r = IP.decide(held_up="2", held_dn="0", side="DOWN", qty=q)
        assert r["act"] is False, q
        assert r["reason"] == "QTY_UNREADABLE", q


def test_unreadable_inventory_fails_closed():
    r = IP.decide(held_up="???", held_dn="0", side="DOWN", qty="1")
    assert r["act"] is False
    assert r["reason"] == "INVENTORY_UNREADABLE"


def test_the_down_heavy_mirror_behaves_identically():
    """0200z and 0230z both ended DOWN-heavy; the rule must be symmetric."""
    red = IP.decide(held_up="0", held_dn="1.85", side="UP", qty="1.85")
    assert red["act"] is True and red["class"] == "REDUCING"
    add = IP.decide(held_up="0", held_dn="1.85", side="DOWN", qty="1.85")
    assert add["act"] is False and add["class"] == "INCREASING"


def test_this_module_moves_no_limit():
    """Guard against scope creep: it must expose no exposure, loss, cash,
    concurrency or permission knob.
    """
    banned = ("max_total_exposure", "max_live_orders", "daily_loss",
              "max_cash", "taker", "pair_ceiling", "residual_cap")
    names = [n.lower() for n in dir(IP)]
    for b in banned:
        assert not any(b in n for n in names), b

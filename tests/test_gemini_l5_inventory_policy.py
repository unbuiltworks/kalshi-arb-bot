from decimal import Decimal as D

from src.gemini_l5_inventory_policy import (
    completion_budget,
    inventory_decision,
    locked_pair_edge,
)


def test_completion_budget_preserves_pair_floor_after_rounded_maker_fee():
    # Prior q=1 failure shape: if YES filled around 65c plus a 1c maker fee,
    # completion must refuse NO prices that destroy the locked pair floor.
    budget = completion_budget(
        held_side="YES",
        held_price=D("0.65"),
        held_fee=D("0.01"),
        pair_floor=D("0.01"),
        price_increment=D("0.01"),
    )
    assert budget.completion_side == "NO"
    assert budget.max_completion_price is not None
    assert locked_pair_edge(D("0.65"), D("0.01"), budget.max_completion_price) >= D("0.01")
    assert locked_pair_edge(D("0.65"), D("0.01"), budget.max_completion_price + D("0.01")) < D("0.01")


def test_residual_yes_forces_no_pair_completion_instead_of_more_yes():
    decision = inventory_decision(
        yes_qty=D("1"),
        no_qty=D("0"),
        residual_unit_price=D("0.65"),
        residual_unit_fee=D("0.01"),
        complementary_resting_quote=D("0.25"),
        complementary_passive_ceiling=D("0.31"),
        seconds_to_expiry=180,
        new_inventory_cutoff_s=45,
    )
    assert decision.mode == "PAIR_COMPLETE"
    assert decision.target_side == "NO"
    assert decision.allow_new_directional_inventory is False
    assert decision.quote is not None
    assert decision.locked_edge_if_fill is not None
    assert decision.locked_edge_if_fill >= D("0.01")


def test_pair_completion_waits_if_complement_is_too_expensive():
    decision = inventory_decision(
        yes_qty=D("1"),
        no_qty=D("0"),
        residual_unit_price=D("0.65"),
        residual_unit_fee=D("0.01"),
        complementary_resting_quote=D("0.40"),
        complementary_passive_ceiling=D("0.45"),
        seconds_to_expiry=180,
        new_inventory_cutoff_s=45,
    )
    assert decision.mode == "WAIT_FOR_COMPLEMENT"
    assert decision.target_side == "NO"
    assert decision.quote is None
    assert decision.allow_new_directional_inventory is False


def test_balanced_inventory_can_accumulate_early_but_freezes_late():
    early = inventory_decision(
        yes_qty=D("1"),
        no_qty=D("1"),
        residual_unit_price=None,
        residual_unit_fee=None,
        complementary_resting_quote=None,
        complementary_passive_ceiling=None,
        seconds_to_expiry=120,
        new_inventory_cutoff_s=45,
    )
    assert early.mode == "ACCUMULATE"
    assert early.allow_new_directional_inventory is True

    late = inventory_decision(
        yes_qty=D("1"),
        no_qty=D("1"),
        residual_unit_price=None,
        residual_unit_fee=None,
        complementary_resting_quote=None,
        complementary_passive_ceiling=None,
        seconds_to_expiry=30,
        new_inventory_cutoff_s=45,
    )
    assert late.mode == "FREEZE_LATE_FLAT"
    assert late.allow_new_directional_inventory is False


def test_missing_residual_cost_basis_blocks_new_risk():
    decision = inventory_decision(
        yes_qty=D("2"),
        no_qty=D("1"),
        residual_unit_price=None,
        residual_unit_fee=None,
        complementary_resting_quote=D("0.20"),
        complementary_passive_ceiling=D("0.30"),
        seconds_to_expiry=100,
        new_inventory_cutoff_s=45,
    )
    assert decision.mode == "HOLD_RESIDUAL_NO_COST_BASIS"
    assert decision.allow_new_directional_inventory is False

from decimal import Decimal as D

import pytest

from src import gemini_incentive_mm_dynamic_complement as policy


def test_completed_pair_edge_excludes_rewards_and_uses_maker_fees():
    assert policy.completed_pair_edge(filled_price=D("0.35"), complement_price=D("0.60")) == D("0.03")


def test_max_edge_safe_price_spends_only_available_completion_budget():
    assert policy.max_edge_safe_complement_price(
        filled_price=D("0.33"), min_final_edge=D("0.03")
    ) == D("0.62")
    assert policy.completed_pair_edge(filled_price=D("0.33"), complement_price=D("0.63")) < D("0.03")


def test_previous_strike_distance_probe_had_no_3c_rescue_room_left():
    plan = policy.dynamic_complement_plan(
        filled_outcome="NO", filled_price=D("0.35"), current_complement_price=D("0.60"),
        yes_bid=D("0.59"), yes_ask=D("0.67"), min_final_edge=D("0.03"),
    )
    assert plan["action"] == "HOLD_OR_CANCEL"
    assert plan["target_price"] == D("0.60")
    assert plan["edge_ceiling"] == D("0.60")


def test_preserving_raw_entry_edge_creates_three_cent_rescue_budget():
    plan = policy.dynamic_complement_plan(
        filled_outcome="NO", filled_price=D("0.33"), current_complement_price=D("0.59"),
        yes_bid=D("0.59"), yes_ask=D("0.67"), min_final_edge=D("0.03"),
    )
    assert plan["action"] == "REPRICE_MAKER_COMPLEMENT"
    assert plan["target_price"] == D("0.62")
    assert plan["price_improvement"] == D("0.03")
    assert plan["final_edge"] == D("0.03")


def test_one_cent_floor_can_spend_more_edge_but_never_go_negative():
    plan = policy.dynamic_complement_plan(
        filled_outcome="NO", filled_price=D("0.33"), current_complement_price=D("0.59"),
        yes_bid=D("0.59"), yes_ask=D("0.67"), min_final_edge=D("0.01"),
    )
    assert plan["action"] == "REPRICE_MAKER_COMPLEMENT"
    assert plan["target_price"] == D("0.64")
    assert plan["final_edge"] == D("0.01")


def test_profitable_taker_rescue_finishes_immediately():
    # NO maker filled at 33c. If YES can be taken at 61c, maker+taker fees still
    # leave 3c locked, so waiting in the maker queue is inferior.
    plan = policy.dynamic_complement_plan(
        filled_outcome="NO", filled_price=D("0.33"), current_complement_price=D("0.59"),
        yes_bid=D("0.58"), yes_ask=D("0.61"), min_final_edge=D("0.03"),
    )
    assert plan["action"] == "TAKE_COMPLEMENT_NOW"
    assert plan["missing_outcome"] == "YES"
    assert plan["taker_price"] == D("0.61")
    assert plan["final_edge"] == D("0.03")


def test_maker_fallback_used_when_taker_fee_breaks_floor():
    # Taking YES at 63c leaves only 1c, but posting at 62c still leaves 3c.
    plan = policy.dynamic_complement_plan(
        filled_outcome="NO", filled_price=D("0.33"), current_complement_price=D("0.59"),
        yes_bid=D("0.58"), yes_ask=D("0.63"), min_final_edge=D("0.03"),
    )
    assert plan["action"] == "REPRICE_MAKER_COMPLEMENT"
    assert plan["target_price"] == D("0.62")
    assert plan["passive_ceiling"] == D("0.62")
    assert plan["final_edge"] == D("0.03")
    assert plan["taker_final_edge"] == D("0.01")


def test_yes_fill_maps_correctly_to_no_book_ceiling_and_taker_price():
    assert policy.maker_passive_ceiling(
        missing_outcome="NO", yes_bid=D("0.59"), yes_ask=D("0.67")
    ) == D("0.40")
    assert policy.complement_taker_price(
        missing_outcome="NO", yes_bid=D("0.59"), yes_ask=D("0.67")
    ) == D("0.41")


def test_invalid_final_edge_floor_rejected():
    with pytest.raises(ValueError):
        policy.max_edge_safe_complement_price(filled_price=D("0.33"), min_final_edge=D("-0.01"))

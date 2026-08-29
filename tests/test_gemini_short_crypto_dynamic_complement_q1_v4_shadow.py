from decimal import Decimal as D

from src import gemini_short_crypto_dynamic_complement_q1_v4_shadow as dyn
from src import gemini_incentive_mm_dynamic_complement as policy


def test_large_raw_edge_spends_to_hard_two_cent_floor_or_passive_ceiling():
    model = dyn.front_queue_pair_model(D("0.20"), D("0.64"))
    assert model["raw_yes_quote"] == D("0.20")
    assert model["raw_no_quote"] == D("0.36")
    assert model["raw_modeled_edge"] == D("0.42")
    assert model["quote_action"] == "IMPROVE_INITIAL_MAKERS"
    assert model["modeled_edge"] >= D("0.02")
    assert model["modeled_edge"] < D("0.10")
    assert model["yes_quote"] <= model["yes_passive_ceiling"]
    assert model["no_quote"] <= model["no_passive_ceiling"]


def test_five_to_ten_cent_raw_edge_now_spends_toward_front():
    model = dyn.front_queue_pair_model(D("0.59"), D("0.67"))
    assert model["raw_modeled_edge"] == D("0.06")
    assert model["quote_action"] == "IMPROVE_INITIAL_MAKERS"
    assert model["yes_quote"] > D("0.59") or model["no_quote"] > D("0.33")
    assert model["modeled_edge"] >= D("0.02")


def test_below_raw_five_cent_gate_remains_ineligible_and_unmoved():
    model = dyn.front_queue_pair_model(D("0.59"), D("0.64"))
    assert model["raw_modeled_edge"] < D("0.05")
    assert model["quote_action"] == "INELIGIBLE_RAW_EDGE"
    assert model["yes_quote"] == D("0.59")
    assert model["no_quote"] == D("0.36")


def test_shadow_front_quotes_remain_maker_passive_and_inside_band():
    bid, ask = D("0.20"), D("0.64")
    model = dyn.front_queue_pair_model(bid, ask)
    yes_ceiling = policy.maker_passive_ceiling(
        missing_outcome="YES", yes_bid=bid, yes_ask=ask, increment=D("0.01")
    )
    no_ceiling = policy.maker_passive_ceiling(
        missing_outcome="NO", yes_bid=bid, yes_ask=ask, increment=D("0.01")
    )
    assert dyn.MIN_PRICE <= model["yes_quote"] <= min(yes_ceiling, dyn.MAX_PRICE)
    assert dyn.MIN_PRICE <= model["no_quote"] <= min(no_ceiling, dyn.MAX_PRICE)
    assert model["modeled_edge"] >= dyn.FRONT_QUEUE_TARGET_EDGE == D("0.02")


def test_v4_shadow_does_not_lower_raw_eligibility_gate():
    assert dyn.ENTRY_PAIR_EDGE == D("0.05")
    assert dyn.FRONT_QUEUE_TARGET_EDGE == dyn.FINAL_PAIR_EDGE == D("0.02")

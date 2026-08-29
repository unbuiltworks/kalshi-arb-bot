from decimal import Decimal as D

from src import gemini_short_crypto_dynamic_complement_q1_v3 as dyn
from src import gemini_incentive_mm_dynamic_complement as policy


def test_large_raw_edge_is_spent_to_ten_cent_entry_reserve():
    # Latest live-style book: YES 20 bid / 64 ask => raw NO 36 and 42c edge.
    model = dyn.edge_spending_pair_model(D("0.20"), D("0.64"))
    assert model["raw_yes_quote"] == D("0.20")
    assert model["raw_no_quote"] == D("0.36")
    assert model["raw_modeled_edge"] == D("0.42")
    assert model["quote_action"] == "IMPROVE_INITIAL_MAKERS"
    assert model["yes_quote"] == D("0.36")
    assert model["no_quote"] == D("0.52")
    assert model["yes_improvement"] == D("0.16")
    assert model["no_improvement"] == D("0.16")
    assert model["modeled_edge"] == dyn.INITIAL_TARGET_EDGE == D("0.10")


def test_small_eligible_raw_edge_is_not_spent_below_target():
    # Prior 59/67 book had only 6c raw edge. V3 leaves it unchanged rather than
    # pretending it can preserve a 10c reserve while improving queue position.
    model = dyn.edge_spending_pair_model(D("0.59"), D("0.67"))
    assert model["raw_modeled_edge"] == D("0.06")
    assert model["quote_action"] == "HOLD_RAW"
    assert model["yes_quote"] == D("0.59")
    assert model["no_quote"] == D("0.33")
    assert model["modeled_edge"] == D("0.06")


def test_aggressive_quotes_remain_maker_passive_and_inside_band():
    bid, ask = D("0.20"), D("0.64")
    model = dyn.edge_spending_pair_model(bid, ask)
    yes_ceiling = policy.maker_passive_ceiling(
        missing_outcome="YES", yes_bid=bid, yes_ask=ask, increment=D("0.01")
    )
    no_ceiling = policy.maker_passive_ceiling(
        missing_outcome="NO", yes_bid=bid, yes_ask=ask, increment=D("0.01")
    )
    assert dyn.MIN_PRICE <= model["yes_quote"] <= min(yes_ceiling, dyn.MAX_PRICE)
    assert dyn.MIN_PRICE <= model["no_quote"] <= min(no_ceiling, dyn.MAX_PRICE)


def test_v3_does_not_change_post_fill_risk_bounds():
    assert dyn.ENTRY_PAIR_EDGE == D("0.05")
    assert dyn.INITIAL_TARGET_EDGE == D("0.10")
    assert dyn.FINAL_PAIR_EDGE == D("0.02")
    assert dyn.INITIAL_WAIT_SECONDS == 60.0
    assert dyn.RESCUE_SECONDS == 30.0
    assert dyn.MAX_REPRICES == 6

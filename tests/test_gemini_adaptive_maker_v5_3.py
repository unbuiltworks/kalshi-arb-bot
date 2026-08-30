from decimal import Decimal as D

from src.gemini_adaptive_maker_policy_v5_3 import (
    DEFAULT_MAX_LOSS,
    pair_profit,
    select_plan,
    standing_rescue_for_plan,
)
from src.gemini_prediction_depth_cache_v5_3 import DepthSnapshot, parse_depth5


def snap(*, bids, asks, age_ms=10.0):
    return DepthSnapshot(
        bids=tuple((D(str(p)), D(str(q))) for p, q in bids),
        asks=tuple((D(str(p)), D(str(q))) for p, q in asks),
        age_ms=age_ms,
        received_ns=1,
        sequence=1,
    )


def test_depth5_parser_sorts_and_aggregates_levels():
    parsed = parse_depth5(
        {
            "bids": [["0.12", "2"], ["0.13", "3"], ["0.13", "4"], ["bad"]],
            "asks": [["0.47", "2"], ["0.46", "5"], ["0.46", "6"]],
        }
    )
    assert parsed is not None
    bids, asks = parsed
    assert bids[0] == (D("0.13"), D("7"))
    assert bids[1] == (D("0.12"), D("2"))
    assert asks[0] == (D("0.46"), D("11"))
    assert asks[1] == (D("0.47"), D("2"))


def test_externalize_owned_improved_yes_falls_through_to_next_bid():
    s = snap(bids=[("0.14", "10"), ("0.13", "100")], asks=[("0.46", "100")])
    external = s.externalized(own_yes_price=D("0.14"), own_yes_quantity=D("10"))
    assert external is not None
    assert external.bid == D("0.13")
    assert external.bid_size == D("100")


def test_externalize_owned_no_uses_complemented_yes_ask_level():
    s = snap(bids=[("0.13", "100")], asks=[("0.39", "10"), ("0.46", "100")])
    external = s.externalized(own_no_price=D("0.61"), own_no_quantity=D("10"))
    assert external is not None
    assert external.ask == D("0.46")
    assert external.ask_size == D("100")


def test_trade_edge_planner_does_not_force_rebate_band_and_keeps_q10_safe():
    # Live-public shape observed during the Aug 30 diagnostic. The old reward-band
    # interpretation pushed YES from 13c to >=20c and made q10 look unsafe. Pure
    # trade-edge planning can keep a safe q10 clip without counting any rebate.
    s = snap(bids=[("0.13", "100")], asks=[("0.46", "100")])
    plan = select_plan(snap=s, increment=D("0.01"))
    assert plan is not None
    assert plan.quantity == D("10")
    assert plan.yes_quote == D("0.14")
    assert plan.no_quote == D("0.54")
    assert plan.pair_profit == D("3.12")
    assert plan.edge_floor == D("0.50")
    assert plan.worst_rescue_loss <= DEFAULT_MAX_LOSS
    assert not plan.yes_rebate_eligible
    assert plan.no_rebate_eligible


def test_planner_reduces_quantity_when_external_rescue_depth_is_small():
    s = snap(bids=[("0.13", "3")], asks=[("0.46", "3")])
    plan = select_plan(snap=s, increment=D("0.01"))
    assert plan is not None
    assert plan.quantity == D("3")
    assert plan.worst_rescue_loss <= DEFAULT_MAX_LOSS


def test_planner_rejects_narrow_book_without_five_cents_per_contract():
    s = snap(bids=[("0.49", "100")], asks=[("0.51", "100")])
    assert select_plan(snap=s, increment=D("0.01")) is None


def test_standing_check_removes_both_owned_quotes_and_uses_next_external_depth():
    initial = snap(bids=[("0.13", "100")], asks=[("0.46", "100")])
    plan = select_plan(snap=initial, increment=D("0.01"))
    assert plan is not None and plan.quantity == D("10")

    # Public book after our orders appear: improved YES 14c is entirely ours; NO
    # 54c sits at transformed YES ask 46c alongside 100 external contracts.
    live = snap(
        bids=[("0.14", "10"), ("0.13", "100")],
        asks=[("0.46", "110"), ("0.47", "50")],
    )
    standing = standing_rescue_for_plan(
        plan=plan,
        snap=live,
        increment=D("0.01"),
        yes_resting=True,
        no_resting=True,
    )
    assert standing is not None
    assert standing.safe
    assert standing.worst_current_loss <= DEFAULT_MAX_LOSS


def test_standing_check_fails_closed_when_owned_top_exhausts_visible_bid_ladder():
    initial = snap(bids=[("0.13", "100")], asks=[("0.46", "100")])
    plan = select_plan(snap=initial, increment=D("0.01"))
    assert plan is not None
    live = snap(bids=[("0.14", "10")], asks=[("0.46", "110")])
    assert (
        standing_rescue_for_plan(
            plan=plan,
            snap=live,
            increment=D("0.01"),
            yes_resting=True,
            no_resting=True,
        )
        is None
    )


def test_pair_profit_excludes_rebates():
    assert pair_profit(yes_quote=D("0.14"), no_quote=D("0.54"), quantity=D("10")) == D("3.12")

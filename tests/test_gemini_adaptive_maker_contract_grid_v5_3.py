from decimal import Decimal as D
from types import SimpleNamespace

from src import gemini_adaptive_maker_contract_grid_v5_3 as grid
from src import gemini_adaptive_maker_policy_v5_3 as policy
from src.gemini_prediction_depth_cache_v5_3 import DepthSnapshot


def event(*, qmin="1.00", qinc="0.01", pinc="0.01"):
    return SimpleNamespace(
        quantity_minimum=D(qmin),
        quantity_increment=D(qinc),
        price_increment=D(pinc),
    )


def snap():
    return DepthSnapshot(
        bids=((D("0.13"), D("100")),),
        asks=((D("0.46"), D("100")),),
        age_ms=1.0,
        received_ns=1,
        sequence=1,
    )


def test_decimal_quantity_increment_accepts_integer_q1_to_q10():
    assert grid.valid_integer_quantities(event(qmin="1.00", qinc="0.01")) == tuple(range(1, 11))


def test_minimum_filters_small_integer_clips_but_keeps_valid_larger_clips():
    assert grid.valid_integer_quantities(event(qmin="3.00", qinc="0.01")) == tuple(range(3, 11))


def test_non_dividing_increment_filters_invalid_integer_clips():
    assert grid.valid_integer_quantities(event(qmin="1", qinc="2")) == (2, 4, 6, 8, 10)


def test_invalid_metadata_fails_closed():
    assert grid.valid_integer_quantities(event(qinc="0")) == ()
    assert grid.valid_integer_quantities(event(pinc="0")) == ()


def test_event_specific_planner_uses_largest_valid_safe_quantity():
    e = event(qmin="1", qinc="2")
    plan = grid.select_plan_for_event(e, snap=snap(), increment=D("0.01"))
    assert plan is not None
    assert plan.quantity == D("10")


def test_grid_aware_drop_in_never_selects_invalid_quantity(monkeypatch):
    e = event(qmin="1", qinc="2")
    assert grid.event_contract_ok(e)
    monkeypatch.setattr(policy, "select_plan", grid.select_plan_grid_aware)
    plan = policy.select_plan(snap=snap(), increment=D("0.01"))
    assert plan is not None
    assert int(plan.quantity) in {2, 4, 6, 8, 10}

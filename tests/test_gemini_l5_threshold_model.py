from decimal import Decimal as D

from src.gemini_l5_threshold_model import maker_rebate_estimate, plan_threshold_quotes


def test_yes_favored_gets_one_tick_skew_when_pair_floor_survives():
    plan = plan_threshold_quotes(D("0.60"), D("0.65"))
    assert plan.favored_side == "YES"
    assert plan.favored_probability == D("0.625")
    assert plan.yes_quote == D("0.61")
    assert plan.no_quote == D("0.35")
    assert plan.quote_skew_ticks == 1
    assert plan.modeled_edge == D("0.02")
    assert plan.actionable_live_q1 is True


def test_no_favored_gets_one_tick_skew_when_pair_floor_survives():
    plan = plan_threshold_quotes(D("0.35"), D("0.40"))
    assert plan.favored_side == "NO"
    assert plan.favored_probability == D("0.625")
    assert plan.yes_quote == D("0.35")
    assert plan.no_quote == D("0.61")
    assert plan.quote_skew_ticks == 1
    assert plan.modeled_edge == D("0.02")
    assert plan.actionable_live_q1 is True


def test_balanced_market_is_not_live_actionable_even_with_pair_edge():
    plan = plan_threshold_quotes(D("0.48"), D("0.52"))
    assert plan.favored_side is None
    assert plan.shadow_target_units == 0
    assert plan.actionable_live_q1 is False


def test_rebate_band_is_a_live_gate_for_incentive_probe():
    plan = plan_threshold_quotes(D("0.81"), D("0.85"))
    assert plan.favored_side == "YES"
    assert plan.yes_rebate_eligible is False
    assert plan.no_rebate_eligible is False
    assert plan.actionable_live_q1 is False


def test_q1_rebate_rounds_to_zero_near_midpoint():
    raw, rounded = maker_rebate_estimate(D("0.50"), D("1"))
    assert raw == D("0.00525")
    assert rounded == D("0.00")


def test_q10_rebate_is_measurable_and_shadow_units_scale():
    raw, rounded = maker_rebate_estimate(D("0.50"), D("10"))
    assert raw == D("0.05250")
    assert rounded == D("0.05")
    plan = plan_threshold_quotes(D("0.70"), D("0.75"))
    assert plan.shadow_target_units == 2

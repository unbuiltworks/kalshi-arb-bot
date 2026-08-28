from decimal import Decimal as D

from src.kalshi_l5_threshold_model import plan_kalshi_threshold_quotes


def test_yes_favored_skews_one_tick_passively():
    plan = plan_kalshi_threshold_quotes(D("0.60"), D("0.65"))
    assert plan.favored_side == "YES"
    assert plan.favored_probability == D("0.625")
    assert plan.yes_quote == D("0.61")
    assert plan.no_quote == D("0.35")
    assert plan.quote_skew_ticks == 1
    assert plan.shadow_target_units == 1
    assert plan.passive is True


def test_no_favored_skews_one_tick_passively():
    plan = plan_kalshi_threshold_quotes(D("0.35"), D("0.40"))
    assert plan.favored_side == "NO"
    assert plan.favored_probability == D("0.625")
    assert plan.yes_quote == D("0.35")
    assert plan.no_quote == D("0.61")
    assert plan.quote_skew_ticks == 1
    assert plan.passive is True


def test_balanced_market_has_no_directional_action():
    plan = plan_kalshi_threshold_quotes(D("0.48"), D("0.52"))
    assert plan.favored_side is None
    assert plan.shadow_target_units == 0
    assert plan.quote_skew_ticks == 0


def test_shadow_units_scale_with_probability():
    assert plan_kalshi_threshold_quotes(D("0.70"), D("0.75")).shadow_target_units == 2
    assert plan_kalshi_threshold_quotes(D("0.80"), D("0.85")).shadow_target_units == 3


def test_pair_margin_is_pre_fee_only_math():
    plan = plan_kalshi_threshold_quotes(D("0.60"), D("0.65"))
    assert plan.pair_cost_if_both_fill == D("0.96")
    assert plan.pre_fee_pair_margin == D("0.04")

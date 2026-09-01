from decimal import Decimal as D

from src.kalshi_l5_threshold_model import kalshi_price_step, plan_kalshi_threshold_quotes


TAPERED = [
    {"start": "0.0000", "end": "0.1000", "step": "0.0010"},
    {"start": "0.1000", "end": "0.9000", "step": "0.0100"},
    {"start": "0.9000", "end": "1.0000", "step": "0.0010"},
]


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


def test_tapered_structure_uses_deci_cent_at_tails():
    assert kalshi_price_step(D("0.050"), TAPERED) == D("0.0010")
    assert kalshi_price_step(D("0.500"), TAPERED) == D("0.0100")
    assert kalshi_price_step(D("0.950"), TAPERED) == D("0.0010")


def test_no_favored_tail_skew_uses_subpenny_step():
    plan = plan_kalshi_threshold_quotes(
        D("0.0030"), D("0.0050"), price_ranges=TAPERED
    )
    assert plan.favored_side == "NO"
    assert plan.no_quote == D("0.9960")
    assert plan.quote_step == D("0.0010")
    assert plan.quote_skew_ticks == 1
    assert plan.passive is True

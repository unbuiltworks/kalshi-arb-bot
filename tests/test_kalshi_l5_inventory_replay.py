from decimal import Decimal as D

from src.kalshi_l5_inventory_replay import (
    MarketReplay,
    apply_observation,
    kalshi_general_maker_fee,
)


def row(*, yes_bid, yes_ask, favored, target, yes_quote=None, no_quote=None, ts="t"):
    yes_bid = D(str(yes_bid))
    yes_ask = D(str(yes_ask))
    return {
        "type": "KALSHI_L5_THRESHOLD_SHADOW",
        "ticker": "T",
        "series": "KXBTC15M",
        "observed_at": ts,
        "yes_bid": str(yes_bid),
        "yes_ask": str(yes_ask),
        "favored_side": favored,
        "shadow_target_units": target,
        "shadow_yes_quote": str(yes_quote if yes_quote is not None else yes_bid),
        "shadow_no_quote": str(no_quote if no_quote is not None else D("1") - yes_ask),
    }


def test_touch_is_not_fill_but_strict_trade_through_is():
    s = MarketReplay("T", "KXBTC15M")
    apply_observation(s, row(yes_bid="0.60", yes_ask="0.65", favored="YES", target=1, yes_quote="0.61"))
    assert s.yes_qty == 0
    # Touch at 0.61 is deliberately not credited.
    apply_observation(s, row(yes_bid="0.59", yes_ask="0.61", favored="YES", target=1, yes_quote="0.60"))
    assert s.yes_qty == 0
    # A subsequent ask below the resting 0.60 quote is credited.
    apply_observation(s, row(yes_bid="0.57", yes_ask="0.59", favored="YES", target=1, yes_quote="0.58"))
    assert s.yes_qty == 1
    assert s.yes_cost == D("0.60")


def test_scaling_ladder_can_add_second_unit_after_70_percent():
    s = MarketReplay("T", "KXBTC15M")
    apply_observation(s, row(yes_bid="0.60", yes_ask="0.65", favored="YES", target=1, yes_quote="0.61"))
    apply_observation(s, row(yes_bid="0.58", yes_ask="0.59", favored="YES", target=1, yes_quote="0.58"))
    assert s.yes_qty == 1
    # New 70% regime requests two total YES units.
    apply_observation(s, row(yes_bid="0.70", yes_ask="0.75", favored="YES", target=2, yes_quote="0.71"))
    apply_observation(s, row(yes_bid="0.68", yes_ask="0.69", favored="YES", target=2, yes_quote="0.68"))
    assert s.yes_qty == 2
    assert len(s.fills) == 2


def test_reversal_retains_first_side_and_can_accumulate_opposite():
    s = MarketReplay("T", "KXBTC15M")
    apply_observation(s, row(yes_bid="0.60", yes_ask="0.65", favored="YES", target=1, yes_quote="0.61"))
    apply_observation(s, row(yes_bid="0.58", yes_ask="0.59", favored="YES", target=1, yes_quote="0.58"))
    assert s.yes_qty == 1

    # NO becomes favored; buy-NO quote 0.31 means YES-side equivalent ask is 0.69.
    apply_observation(s, row(yes_bid="0.35", yes_ask="0.40", favored="NO", target=1, no_quote="0.61"))
    # next NO ask = 1 - 0.40 = 0.60, strictly through 0.61 => fill.
    apply_observation(s, row(yes_bid="0.40", yes_ask="0.42", favored="NO", target=1, no_quote="0.58"))
    assert s.no_qty == 1
    assert s.yes_qty == 1
    assert s.side_switches == 1
    assert s.matched_qty == 1
    assert s.residual_qty == 0


def test_portfolio_worst_case_distinguishes_matched_and_directional_inventory():
    s = MarketReplay("T", "KXBTC15M")
    s._fill("YES", D("0.60"), D("2"), "x")
    s._fill("NO", D("0.30"), D("1"), "y")
    assert s.matched_qty == 1
    assert s.matched_vwap_pair_cost == D("0.90")
    assert s.matched_gross_margin == D("0.10")
    assert s.residual_side == "YES"
    assert s.residual_qty == 1
    # Whole portfolio is not locked despite profitable matched inventory.
    assert s.payoff("YES") == D("0.50")
    assert s.payoff("NO") == D("-0.50")


def test_general_maker_fee_scenario_rounds_up_to_cent():
    assert kalshi_general_maker_fee(D("0.50"), D("1")) == D("0.01")
    assert kalshi_general_maker_fee(D("0.50"), D("10")) == D("0.05")

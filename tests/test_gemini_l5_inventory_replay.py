from decimal import Decimal as D

from src.gemini_l5_inventory_replay import MarketReplay, apply_observation


def row(*, yes_bid, yes_ask, favored, target, yes_quote=None, no_quote=None, reward=False):
    yes_bid = D(str(yes_bid))
    yes_ask = D(str(yes_ask))
    return {
        "type": "GEMINI_L5_THRESHOLD_SHADOW",
        "ticker": "BTC05M2608280300",
        "asset": "BTC",
        "horizon": "5m",
        "observed_at": "t",
        "yes_bid": str(yes_bid),
        "yes_ask": str(yes_ask),
        "favored_side": favored,
        "shadow_target_units": target,
        "shadow_yes_quote": str(yes_quote if yes_quote is not None else yes_bid),
        "shadow_no_quote": str(no_quote if no_quote is not None else D("1") - yes_ask),
        "reward_pool_active": reward,
    }


def test_touch_not_fill_trade_through_is_fill():
    s = MarketReplay("T", "BTC", "5m")
    apply_observation(s, row(yes_bid="0.60", yes_ask="0.65", favored="YES", target=1, yes_quote="0.61"))
    apply_observation(s, row(yes_bid="0.59", yes_ask="0.61", favored="YES", target=1, yes_quote="0.60"))
    assert s.yes_qty == 0
    apply_observation(s, row(yes_bid="0.57", yes_ask="0.59", favored="YES", target=1, yes_quote="0.58"))
    assert s.yes_qty == 1
    assert s.yes_cost == D("0.60")
    assert s.maker_fees == D("0.01")


def test_reversal_can_build_both_sides_and_tracks_residual():
    s = MarketReplay("T", "BTC", "15m")
    apply_observation(s, row(yes_bid="0.60", yes_ask="0.65", favored="YES", target=1, yes_quote="0.61"))
    apply_observation(s, row(yes_bid="0.58", yes_ask="0.59", favored="YES", target=1, yes_quote="0.58"))
    assert s.yes_qty == 1
    apply_observation(s, row(yes_bid="0.35", yes_ask="0.40", favored="NO", target=1, no_quote="0.61"))
    apply_observation(s, row(yes_bid="0.40", yes_ask="0.42", favored="NO", target=1, no_quote="0.58"))
    assert s.no_qty == 1
    assert s.matched_qty == 1
    assert s.residual_qty == 0
    assert s.side_switches == 1


def test_three_unit_fill_has_separate_fee_and_modeled_rebate():
    s = MarketReplay("T", "ETH", "5m")
    s._fill("YES", D("0.50"), D("3"), "x")
    assert s.maker_fees == D("0.02")
    assert s.modeled_rebate_raw == D("0.015750")
    assert s.modeled_rebate_cent_rounded == D("0.01")
    assert s.trading_payoff("YES") == D("1.48")
    assert s.modeled_economic_payoff_with_rebate("YES") == D("1.49")


def test_reward_pool_flag_is_context_only():
    s = MarketReplay("T", "ETH", "15m")
    apply_observation(s, row(yes_bid="0.60", yes_ask="0.65", favored="YES", target=1, reward=True))
    assert s.reward_pool_active_observed is True
    assert s.summary()["liquidity_reward_pnl"] is None


def test_directional_inventory_has_negative_worst_case():
    s = MarketReplay("T", "BTC", "5m")
    s._fill("NO", D("0.70"), D("3"), "x")
    assert s.trading_payoff("NO") > 0
    assert s.trading_payoff("YES") < 0
    assert D(s.summary()["worst_case_trading_payoff"]) < 0

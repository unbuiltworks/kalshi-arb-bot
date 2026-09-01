from decimal import Decimal as D

from src import gemini_q10_maxwidth_spotguard_shadow_v2 as v2


def test_liquidity_reward_allows_quotes_outside_maker_rebate_band():
    assert v2.reward_quote_eligible(
        yes_quote=D("0.81"),
        no_quote=D("0.01"),
        mid=D("0.90"),
        max_distance=D("0.10"),
    )


def test_liquidity_reward_rejects_quote_wider_than_reward_limit():
    assert not v2.reward_quote_eligible(
        yes_quote=D("0.79"),
        no_quote=D("0.01"),
        mid=D("0.90"),
        max_distance=D("0.10"),
    )


def test_liquidity_reward_rejects_zero_price():
    assert not v2.reward_quote_eligible(
        yes_quote=D("0.82"),
        no_quote=D("0.00"),
        mid=D("0.91"),
        max_distance=D("0.10"),
    )


def test_evaluate_extreme_zero_price_fails_closed_without_exception():
    out = v2.evaluate_v2(
        yes_quote=D("0.91"),
        no_quote=D("0.00"),
        bid=D("0.94"),
        bid_size=D("100"),
        ask=D("0.96"),
        ask_size=D("100"),
        tick=D("0.01"),
        max_distance=D("0.10"),
    )
    assert out["price_valid"] is False
    assert out["qualified_safe"] is False
    assert out["rescue_safe"] is False
    assert out["rescue"]["reason"] == "INVALID_QUOTE_PRICE"


def test_evaluate_extreme_negative_price_fails_closed_without_exception():
    out = v2.evaluate_v2(
        yes_quote=D("0.92"),
        no_quote=D("-0.01"),
        bid=D("0.95"),
        bid_size=D("100"),
        ask=D("0.97"),
        ask_size=D("100"),
        tick=D("0.01"),
        max_distance=D("0.10"),
    )
    assert out["price_valid"] is False
    assert out["qualified_safe"] is False

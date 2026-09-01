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

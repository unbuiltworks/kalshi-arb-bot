from decimal import Decimal

from src.mlb_kp_rule_risk_live_q1 import (
    D,
    MIN_PROFIT,
    after_first_fill_plan,
    kalshi_payload,
    poly_taker_fee,
    score_q1,
)
from src.strict_mlb_prop_snapshot import StrictCandidate, TopBook


def candidate() -> StrictCandidate:
    return StrictCandidate(
        family="baseball_player_strikeouts",
        poly_slug="astatc-mlb-sf-bos-2026-08-23-k-matwil-gte3",
        kalshi_ticker="KXMLBK-26AUG231315SFBOS-SFMTWILKINSON27-3",
        player="Matt Wilkinson",
        threshold=D("3"),
        game_date="2026-08-23",
        start_skew_seconds=0.0,
    )


def test_current_polymarket_q1_fee_is_six_percent_theta():
    assert poly_taker_fee(D("0.49")) == D("0.01")
    assert poly_taker_fee(D("0.50")) == D("0.02")


def test_wilkinson_like_q1_pair_survives_current_fees():
    p = TopBook(yes_ask=D("0.49"), yes_depth=D("5"), no_ask=D("0.52"), no_depth=D("5"))
    k = TopBook(yes_ask=D("0.56"), yes_depth=D("5"), no_ask=D("0.45"), no_depth=D("5"))
    plan = score_q1(candidate(), p, k, kalshi_fee_multiplier=D("0.5"))
    assert plan is not None
    assert plan.direction == "POLY_YES_KALSHI_NO"
    assert plan.poly_price == D("0.49")
    assert plan.kalshi_price == D("0.45")
    assert plan.locked_profit >= MIN_PROFIT


def test_q1_requires_one_contract_depth():
    p = TopBook(yes_ask=D("0.49"), yes_depth=D("0.5"), no_ask=D("0.99"), no_depth=D("0.5"))
    k = TopBook(yes_ask=D("0.99"), yes_depth=D("0.5"), no_ask=D("0.45"), no_depth=D("0.5"))
    assert score_q1(candidate(), p, k, kalshi_fee_multiplier=D("0.5")) is None


def test_after_first_fill_freezes_first_price_and_reprices_only_hedge():
    p = TopBook(yes_ask=D("0.49"), yes_depth=D("5"), no_ask=D("0.90"), no_depth=D("5"))
    k = TopBook(yes_ask=D("0.90"), yes_depth=D("5"), no_ask=D("0.45"), no_depth=D("5"))
    original = score_q1(candidate(), p, k, kalshi_fee_multiplier=D("0.5"))
    assert original is not None

    p2 = TopBook(yes_ask=D("0.80"), yes_depth=D("5"), no_ask=D("0.90"), no_depth=D("5"))
    k2 = TopBook(yes_ask=D("0.90"), yes_depth=D("5"), no_ask=D("0.44"), no_depth=D("5"))
    fresh = score_q1(candidate(), p2, k2, kalshi_fee_multiplier=D("0.5"))
    assert fresh is not None
    frozen = after_first_fill_plan(original, fresh, first_venue="polymarket", kalshi_fee_multiplier=D("0.5"))
    assert frozen is not None
    assert frozen.poly_price == D("0.49")
    assert frozen.kalshi_price == D("0.44")


def test_after_first_fill_rejects_hedge_that_destroys_three_cent_floor():
    p = TopBook(yes_ask=D("0.49"), yes_depth=D("5"), no_ask=D("0.90"), no_depth=D("5"))
    k = TopBook(yes_ask=D("0.90"), yes_depth=D("5"), no_ask=D("0.45"), no_depth=D("5"))
    original = score_q1(candidate(), p, k, kalshi_fee_multiplier=D("0.5"))
    assert original is not None

    p2 = TopBook(yes_ask=D("0.49"), yes_depth=D("5"), no_ask=D("0.90"), no_depth=D("5"))
    k2 = TopBook(yes_ask=D("0.90"), yes_depth=D("5"), no_ask=D("0.49"), no_depth=D("5"))
    fresh = score_q1(candidate(), p2, k2, kalshi_fee_multiplier=D("0.5"))
    assert fresh is not None
    assert after_first_fill_plan(original, fresh, first_venue="polymarket", kalshi_fee_multiplier=D("0.5")) is None


def test_kalshi_no_buy_uses_yes_space_ask_payload():
    p = TopBook(yes_ask=D("0.49"), yes_depth=D("5"), no_ask=D("0.90"), no_depth=D("5"))
    k = TopBook(yes_ask=D("0.90"), yes_depth=D("5"), no_ask=D("0.45"), no_depth=D("5"))
    plan = score_q1(candidate(), p, k, kalshi_fee_multiplier=D("0.5"))
    assert plan is not None and plan.kalshi_outcome == "NO"
    payload = kalshi_payload(plan)
    assert payload["side"] == "ask"
    assert Decimal(payload["price"]) == D("0.55")
    assert payload["time_in_force"] == "fill_or_kill"
    assert Decimal(payload["count"]) == D("1")

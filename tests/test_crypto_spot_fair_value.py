from decimal import Decimal

import pytest

from src.crypto_spot_fair_value import (
    fair_probability_yes,
    kalshi_maker_fee,
    PessimisticQuote,
    settlement_pnl,
)

D = Decimal


def test_fair_probability_is_half_at_strike():
    p = fair_probability_yes(
        spot=100.0, strike=100.0, seconds_to_expiry=300.0,
        sigma_per_sqrt_second=0.0001,
    )
    assert p == pytest.approx(0.5)


def test_fair_probability_moves_with_spot():
    lo = fair_probability_yes(spot=99.5, strike=100, seconds_to_expiry=300, sigma_per_sqrt_second=0.0005)
    hi = fair_probability_yes(spot=100.5, strike=100, seconds_to_expiry=300, sigma_per_sqrt_second=0.0005)
    assert lo is not None and hi is not None and lo < 0.5 < hi


def test_maker_fee_quadratic_is_zero():
    assert kalshi_maker_fee(D("0.50"), D("1"), fee_type="quadratic", fee_multiplier=D("1")) == D("0")


def test_maker_fee_maker_series_rounds_up_to_cent():
    # 0.0175 * .5 * .5 = .004375 -> conservative next-cent rounding.
    assert kalshi_maker_fee(
        D("0.50"), D("1"), fee_type="quadratic_with_maker_fees", fee_multiplier=D("1")
    ) == D("0.01")


def test_touch_never_fills_even_after_queue_volume():
    q = PessimisticQuote(outcome="YES", price=D("0.58"), queue_ahead=D("2"))
    fill = q.apply_trade(
        trade_yes_price=D("0.58"), trade_no_price=D("0.42"),
        trade_quantity=D("10"), taker_side="no",
    )
    assert fill == 0
    assert q.quantity == 1


def test_trade_through_still_requires_queue_exhaustion():
    q = PessimisticQuote(outcome="YES", price=D("0.58"), queue_ahead=D("5"))
    assert q.apply_trade(
        trade_yes_price=D("0.57"), trade_no_price=D("0.43"),
        trade_quantity=D("2"), taker_side="no",
    ) == 0
    # More eligible volume eventually exhausts 5 ahead + our q=1.
    assert q.apply_trade(
        trade_yes_price=D("0.56"), trade_no_price=D("0.44"),
        trade_quantity=D("4"), taker_side="no",
    ) == 1


def test_bad_stale_bid_fill_is_not_filtered_out():
    # Direction/fair value is irrelevant to fill inference: if sellers trade
    # through our bid and conservative queue is exhausted, we are filled.
    q = PessimisticQuote(outcome="YES", price=D("0.58"), queue_ahead=D("0"))
    assert q.apply_trade(
        trade_yes_price=D("0.54"), trade_no_price=D("0.46"),
        trade_quantity=D("1"), taker_side="no",
    ) == 1


def test_no_bid_uses_no_price_and_yes_taker():
    q = PessimisticQuote(outcome="NO", price=D("0.38"), queue_ahead=D("0"))
    assert q.apply_trade(
        trade_yes_price=D("0.63"), trade_no_price=D("0.37"),
        trade_quantity=D("1"), taker_side="yes",
    ) == 1


def test_settlement_pnl_yes_and_no():
    assert settlement_pnl(
        outcome="YES", price=D("0.58"), quantity=D("1"), result_yes=True, maker_fee=D("0")
    ) == D("0.42")
    assert settlement_pnl(
        outcome="NO", price=D("0.38"), quantity=D("1"), result_yes=True, maker_fee=D("0")
    ) == -D("0.38")

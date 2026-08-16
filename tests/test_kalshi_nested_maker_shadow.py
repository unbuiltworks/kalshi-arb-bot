from decimal import Decimal

from src.kalshi_nested_maker_shadow import (
    Q,
    _orientation,
    maker_then_taker_profit,
    max_maker_price_for_target,
)

D = Decimal


def _q(ticker: str, strike_type: str, strike: str) -> Q:
    return Q(
        ticker=ticker,
        event_ticker="EVENT",
        strike_type=strike_type,
        strike=D(strike),
        yes_bid=D("0.40"),
        yes_ask=D("0.42"),
        no_bid=D("0.58"),
        no_ask=D("0.60"),
        title=ticker,
    )


def test_maker_join_then_taker_hedge_can_lock_positive_profit():
    profit, fee = maker_then_taker_profit(
        maker_price=D("0.40"),
        hedge_price=D("0.40"),
        fee_multiplier=D("1"),
    )
    # q=1 at a 40c taker hedge has a 2c rounded quadratic fee.
    assert fee == D("0.02")
    assert profit == D("0.18")


def test_max_maker_price_hits_requested_target_exactly():
    max_price = max_maker_price_for_target(
        hedge_price=D("0.40"),
        fee_multiplier=D("1"),
        target_profit=D("0.01"),
    )
    profit, _ = maker_then_taker_profit(
        maker_price=max_price,
        hedge_price=D("0.40"),
        fee_multiplier=D("1"),
    )
    assert max_price == D("0.57")
    assert profit == D("0.01")


def test_greater_orientation_is_yes_lower_no_higher():
    lower = _q("LOW", "greater", "60000")
    higher = _q("HIGH", "greater", "61000")
    assert _orientation(lower, higher) == ((lower, "yes"), (higher, "no"))


def test_less_orientation_is_no_lower_yes_higher():
    lower = _q("LOW", "less", "0.10")
    higher = _q("HIGH", "less", "0.20")
    assert _orientation(lower, higher) == ((lower, "no"), (higher, "yes"))


def test_invalid_nested_orientation_fails_closed():
    lower = _q("LOW", "greater", "61000")
    higher = _q("HIGH", "greater", "60000")
    try:
        _orientation(lower, higher)
    except ValueError as exc:
        assert "invalid nested orientation" in str(exc)
    else:
        raise AssertionError("reversed strikes must fail closed")

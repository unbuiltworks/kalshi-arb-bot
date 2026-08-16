from decimal import Decimal

from src.kalshi_nested_maker_aggressive_shadow import (
    most_aggressive_resting_price,
    valid_price_levels,
)

D = Decimal


def test_linear_cent_levels_are_exact():
    levels = valid_price_levels([
        {"start": "0.00", "end": "1.00", "step": "0.01"},
    ])
    assert D("0.00") in levels
    assert D("0.97") in levels
    assert D("1.00") in levels
    assert len(levels) == 101


def test_aggressive_quote_improves_bid_and_preserves_target_ceiling():
    quote = most_aggressive_resting_price(
        best_bid=D("0.26"),
        best_ask=D("1.00"),
        max_price=D("0.97"),
        price_ranges=[{"start": "0.00", "end": "1.00", "step": "0.01"}],
    )
    assert quote == D("0.97")


def test_subpenny_tail_uses_market_step_not_one_cent_assumption():
    quote = most_aggressive_resting_price(
        best_bid=D("0.991"),
        best_ask=D("1.000"),
        max_price=D("0.997"),
        price_ranges=[
            {"start": "0.00", "end": "0.10", "step": "0.001"},
            {"start": "0.10", "end": "0.90", "step": "0.01"},
            {"start": "0.90", "end": "1.00", "step": "0.001"},
        ],
    )
    assert quote == D("0.997")


def test_no_inside_price_fails_closed():
    quote = most_aggressive_resting_price(
        best_bid=D("0.56"),
        best_ask=D("0.57"),
        max_price=D("0.80"),
        price_ranges=[{"start": "0.00", "end": "1.00", "step": "0.01"}],
    )
    assert quote is None


def test_target_ceiling_at_or_below_bid_has_no_zero_queue_quote():
    quote = most_aggressive_resting_price(
        best_bid=D("0.40"),
        best_ask=D("0.60"),
        max_price=D("0.40"),
        price_ranges=[{"start": "0.00", "end": "1.00", "step": "0.01"}],
    )
    assert quote is None

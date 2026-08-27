import pytest

from src.sports_fair_value import (
    compare_binary_price,
    decimal_odds_to_implied,
    proportional_devig,
)


def test_decimal_odds_to_implied():
    assert decimal_odds_to_implied(2.0) == pytest.approx(0.5)


def test_proportional_devig_removes_two_way_vig():
    fair = proportional_devig([1.91, 1.91])
    assert fair[0] == pytest.approx(0.5)
    assert fair[1] == pytest.approx(0.5)
    assert sum(fair) == pytest.approx(1.0)


def test_three_way_devig_sums_to_one():
    fair = proportional_devig([2.1, 3.4, 3.8])
    assert sum(fair) == pytest.approx(1.0)


def test_compare_binary_price_detects_raw_edge():
    result = compare_binary_price(0.64, 0.59, min_edge=0.03)
    assert result.raw_edge == pytest.approx(0.05)
    assert result.passes is True


def test_compare_binary_price_rejects_below_threshold():
    result = compare_binary_price(0.61, 0.59, min_edge=0.03)
    assert result.passes is False

"""The complement-mint mechanism, and the discipline around claiming it."""
import pytest

from analysis.l5.historical_l2 import mint_match as M


def test_a_mint_is_feasible_when_the_two_buyers_cover_a_dollar():
    # L5 buys Up at 0.56; someone bids Down at 0.46 -> together they mint $1
    assert M.feasible("0.56", 0.46) is True
    assert M.feasible("0.56", 0.44) is True          # exactly par
    assert M.feasible("0.52", 0.45) is False         # 0.97, nothing to mint


def test_an_unknown_complement_bid_is_not_an_infeasible_mint():
    """Absent evidence is not evidence of absence -- the row must stay None so
    it is excluded from the rate rather than counted as a negative."""
    assert M.feasible("0.56", None) is None


def test_the_complement_map_comes_from_the_dataset_not_from_a_name():
    d = {"coin": ["btc", "btc", "btc", "btc", "eth", "eth"],
         "market_slug": ["w1", "w1", "w2", "w2", "e1", "e1"],
         "token_id": [11, 22, 33, 44, 55, 66]}
    c = M.complements(d)
    assert c == {"11": "22", "22": "11", "33": "44", "44": "33"}


def test_a_market_without_exactly_two_outcomes_is_left_out():
    """Three tokens under one slug is a data problem, not a complement pair.
    Guessing one would silently pair the wrong books."""
    d = {"coin": ["btc"] * 3, "market_slug": ["w1"] * 3, "token_id": [1, 2, 3]}
    assert M.complements(d) == {}


def test_the_par_boundary_is_inclusive_and_exact():
    """Par itself is feasible: two buyers paying exactly $1.00 between them
    mint a complete set with nothing left over. A strict `>` would push every
    boundary row into the wrong class, and the observed pair sums pile up
    exactly there -- p05 is 1.000000.
    """
    assert M.feasible("0.560000", 0.44) is True
    assert M.feasible("0.999999", 0.000001) is True
    assert M.feasible("0.559999", 0.44) is False


def test_chain_prices_carry_six_decimals_and_still_compare_exactly():
    """Fill prices come off the chain as maker/taker quantized to 6dp, not as
    clean cents, and are compared against a float book price."""
    assert M.feasible("0.405000", 0.60) is True     # 1.005000
    assert M.feasible("0.405000", 0.59) is False    # 0.995000

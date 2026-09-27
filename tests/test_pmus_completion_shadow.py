"""The ladder walk is the load-bearing claim: never assume top-of-book depth."""
from decimal import Decimal as D

from src import pmus_completion_shadow as S


def test_a_fill_inside_the_top_level_pays_the_top_price():
    filled, cost, short = S.walk_ladder([["0.40", "100"]], D(10))
    assert filled == D(10) and cost == D("4.0") and short is False


def test_a_fill_beyond_the_top_level_pays_the_worse_levels_too():
    """The whole point. 150 contracts against 100@0.40 then 100@0.45 must NOT
    cost 150*0.40."""
    filled, cost, short = S.walk_ladder(
        [["0.40", "100"], ["0.45", "100"]], D(150))
    assert filled == D(150)
    assert cost == D(100) * D("0.40") + D(50) * D("0.45")
    assert cost == D("62.5")
    assert cost > D(150) * D("0.40"), "top-of-book was applied beyond its size"
    assert short is False


def test_depth_shortfall_is_short_and_is_not_filled_at_the_touch():
    filled, cost, short = S.walk_ladder([["0.40", "10"]], D(150))
    assert filled == D(10)
    assert cost == D("4.0")
    assert short is True, "a shortfall must be visible, not silently filled"


def test_an_empty_ladder_fills_nothing():
    filled, cost, short = S.walk_ladder([], D(5))
    assert filled == D(0) and cost == D(0) and short is True


def test_zero_and_malformed_levels_are_skipped_not_guessed():
    filled, cost, short = S.walk_ladder(
        [["0.40", "0"], ["bad", "5"], ["0.50", "3"]], D(3))
    assert filled == D(3) and cost == D(3) * D("0.50")


def test_down_levels_are_quoted_in_downs_own_terms():
    """A bid at 0.70 means DOWN is buyable at 0.30."""
    row = {"bids": [["0.70", "25"]], "offers": [["0.72", "40"]]}
    lv = S.buy_levels(row, "down")
    assert lv == [["0.30", "25"]]
    filled, cost, short = S.walk_ladder(lv, D(10))
    assert cost == D(10) * D("0.30")


def test_up_levels_lift_the_offers_at_quoted_price():
    row = {"bids": [["0.70", "25"]], "offers": [["0.72", "40"]]}
    assert S.buy_levels(row, "up") == [["0.72", "40"]]


def test_a_one_sided_book_offers_nothing_to_lift():
    row = {"bids": [["0.99", "12979"]], "offers": []}
    assert S.buy_levels(row, "up") == []
    filled, _, short = S.walk_ladder(S.buy_levels(row, "up"), D(1))
    assert filled == D(0) and short is True


def test_modeled_rebate_is_separate_and_uses_the_maker_theta():
    r = S.modeled_maker_rebate(D("0.5"), D(100))
    assert r == S.THETA_MAKER_MODELED * D(100) * D("0.5") * D("0.5")
    assert S.THETA_MAKER_MODELED != S.THETA_TAKER


def test_taker_fee_matches_the_venue_formula():
    assert S.taker_fee(D("0.5"), D(100)) == S.THETA_TAKER * D(100) * D("0.25")

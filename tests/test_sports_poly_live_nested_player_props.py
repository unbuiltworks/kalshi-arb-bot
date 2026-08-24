from decimal import Decimal

from src.sports_poly_live_nested_player_props import _is_integer_threshold, score_prop_pair
from src.strict_mlb_prop_snapshot import TopBook

D = Decimal


def _book(yes_ask: str, yes_depth: str, no_ask: str, no_depth: str) -> TopBook:
    return TopBook(D(yes_ask), D(yes_depth), D(no_ask), D(no_depth))


def test_nested_player_prop_math_and_depth():
    low = _book("0.40", "10", "0.61", "10")
    high = _book("0.25", "10", "0.76", "10")
    row = score_prop_pair(D("2"), low, D("4"), high)
    assert row is not None
    assert row["low_threshold"] == "2"
    assert row["high_threshold"] == "4"
    assert row["low_yes_ask"] == "0.40"
    assert row["high_no_ask"] == "0.76"


def test_nested_player_prop_requires_ordered_thresholds():
    book = _book("0.5", "5", "0.5", "5")
    assert score_prop_pair(D("4"), book, D("2"), book) is None


def test_nested_player_prop_requires_integer_gte_thresholds():
    book = _book("0.5", "5", "0.5", "5")
    assert _is_integer_threshold(D("1"))
    assert _is_integer_threshold(D("4"))
    assert not _is_integer_threshold(D("1.5"))
    assert not _is_integer_threshold(D("0"))
    assert score_prop_pair(D("1.5"), book, D("2.5"), book) is None


def test_nested_player_prop_requires_q1_on_yes_low_and_no_high():
    low = _book("0.4", "0.5", "0.6", "10")
    high = _book("0.2", "10", "0.8", "10")
    assert score_prop_pair(D("1"), low, D("2"), high) is None

    low = _book("0.4", "10", "0.6", "10")
    high = _book("0.2", "10", "0.8", "0.5")
    assert score_prop_pair(D("1"), low, D("2"), high) is None

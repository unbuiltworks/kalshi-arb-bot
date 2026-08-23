from decimal import Decimal

from src.sports_poly_live_nested_dutch import score_pair
from src.strict_mlb_prop_snapshot import TopBook

D = Decimal


def test_nested_total_pair_guarantee_math_and_depth():
    low = TopBook(yes_ask=D("0.42"), yes_depth=D("5"), no_ask=D("0.59"), no_depth=D("5"))
    high = TopBook(yes_ask=D("0.30"), yes_depth=D("5"), no_ask=D("0.71"), no_depth=D("5"))
    row = score_pair(D("7.5"), low, D("8.5"), high)
    assert row is not None
    assert row["low_threshold"] == "7.5"
    assert row["high_threshold"] == "8.5"


def test_requires_ordered_thresholds():
    book = TopBook(yes_ask=D("0.5"), yes_depth=D("5"), no_ask=D("0.5"), no_depth=D("5"))
    assert score_pair(D("8.5"), book, D("7.5"), book) is None


def test_requires_q1_depth_on_both_legs():
    low = TopBook(yes_ask=D("0.4"), yes_depth=D("0.5"), no_ask=D("0.6"), no_depth=D("5"))
    high = TopBook(yes_ask=D("0.3"), yes_depth=D("5"), no_ask=D("0.7"), no_depth=D("5"))
    assert score_pair(D("7.5"), low, D("8.5"), high) is None

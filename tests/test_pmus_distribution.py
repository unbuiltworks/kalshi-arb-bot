"""The distribution statistics must be right, and must expose a one-window mean."""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pmus_distribution import describe, median          # noqa: E402


def test_median_odd_and_even():
    assert median([D(1), D(3), D(2)]) == D(2)
    assert median([D(1), D(2), D(3), D(4)]) == D("2.5")
    assert median([]) == D(0)


def test_the_n13_case_that_motivated_this_module():
    """The real PROFIT_LOCK_5 q50 figures at n=13: one window carries the mean."""
    vals = [D(x) for x in ("5.6326", "5.1496", "5.8911", "-4.3004", "-0.0688",
                           "9.6535", "8.9049", "8.1848", "-7.6362", "6.9699",
                           "10.6838", "-10.6526", "54.0176")]
    labels = ["1830z", "1845z", "1900z", "1915z", "1930z", "1945z", "2000z",
              "2030z", "2045z", "2115z", "2215z", "2245z", "2300z"]
    d = describe(vals, labels=labels)
    assert d["n"] == 13
    assert d["mean"] == "7.1100"
    assert d["median"] == "5.8911"
    assert d["best_window"]["window"] == "2300z"
    assert d["worst_window"]["window"] == "2245z"
    assert d["mean_excluding_best_window"] == "3.2010"
    # the whole point: the best window alone is a majority-sized share of total
    assert D(d["best_window_share_of_total_pnl"]) > D("0.5")
    assert d["positive_window_rate"] == "9 of 13"


def test_trimmed_mean_drops_one_from_each_end():
    d = describe([D(-100), D(1), D(2), D(3), D(100)])
    assert d["trimmed_mean_drop_1_each_end"] == "2.0000"
    assert d["mean"] == "1.2000"


def test_trimmed_mean_undefined_below_three():
    assert describe([D(1), D(2)])["trimmed_mean_drop_1_each_end"] is None


def test_share_of_total_is_withheld_when_the_total_is_not_positive():
    """A share of a negative total is misleading, so it is not reported."""
    d = describe([D(-5), D(2)])
    assert d["best_window_share_of_total_pnl"] is None


def test_lock_hit_rate_is_reported_when_supplied():
    d = describe([D(1), D(2), D(3)], locked=[True, False, True])
    assert d["lock_hit_rate"] == "2 of 3"


def test_empty_is_not_a_crash():
    assert describe([])["n"] == 0

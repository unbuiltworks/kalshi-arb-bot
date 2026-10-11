"""Step 4 came back 12/12 OBSERVED and it means nothing: the same sweep finds
liquidity at 1175 of 1176 OTHER prices. These tests pin the control that keeps
a constant from being read as a signal."""
from decimal import Decimal as D

import pytest

from analysis.l5.historical_l2 import pre_fill_complement as PF
from analysis.l5.historical_l2.bbo_offset import Book


def _book(rows):
    b = Book()
    for t, bids in rows:
        b.add(t, {"bid_prices": [p for p, _ in bids],
                  "bid_sizes": [s for _, s in bids],
                  "ask_prices": [], "ask_sizes": []})
    b.sort()
    return b


def test_size_at_the_price_before_the_fill_is_observed():
    b = _book([(100.0, [(0.45, 320.0)]), (110.0, [(0.45, 200.0)])])
    r = PF.scan_before(b, D("0.45"), until=120.0)
    assert r["label"] == PF.OBSERVED
    assert r["max_bid_size_at_price"] == "320.0"


def test_a_readable_book_with_no_size_there_is_not_observed():
    b = _book([(100.0, [(0.30, 5.0)]), (110.0, [(0.31, 5.0)])])
    assert PF.scan_before(b, D("0.45"), until=120.0)["label"] == PF.NOT_OBSERVED


def test_no_coverage_is_insufficient_not_an_absence():
    """An absence we could not have SEEN is not an absence -- collapsing it
    into NOT_OBSERVED would manufacture evidence for the reactive model."""
    assert PF.scan_before(Book(), D("0.45"), until=120.0)["label"] == PF.INSUFFICIENT


def test_a_snapshot_older_than_the_resync_bound_is_insufficient():
    b = _book([(10.0, [(0.45, 100.0)])])
    r = PF.scan_before(b, D("0.45"), until=120.0)     # 110s stale
    assert r["label"] == PF.INSUFFICIENT
    assert "resync bound" in r["why"]


def test_liquidity_is_never_attributed_to_a_trader():
    b = _book([(100.0, [(0.45, 320.0)])])
    why = PF.scan_before(b, D("0.45"), until=120.0)["why"]
    assert "NOT asserted to be L5's order" in why
    assert PF.OBSERVED.startswith("PRE_FIRST_FILL_LIQUIDITY_AT_COMPLEMENT_PRICE")
    assert "L5_WAS_RESTING" not in PF.OBSERVED


# ------------------------------------------------ the control is the point

def test_a_book_quoted_across_the_whole_grid_is_non_discriminating():
    """What actually happened: depth at every price, so OBSERVED everywhere."""
    full = [(p / 100, 50.0) for p in range(1, 100)]
    b = _book([(100.0, full)])
    c = PF.control(b, D("0.45"), until=120.0)
    assert c["observed_share"] == 1.0
    d = PF.discrimination({PF.OBSERVED: 12}, c["observed_share"])
    assert d["verdict"] == "NON_DISCRIMINATING"
    assert "must NOT be cited as support for either arm" in d["why"]


def test_a_sparse_book_would_have_been_discriminating():
    """Proves the verdict is driven by the control, not hardcoded."""
    b = _book([(100.0, [(0.45, 320.0), (0.46, 10.0)])])
    c = PF.control(b, D("0.45"), until=120.0)
    assert c["observed_share"] < PF.VACUOUS_ABOVE
    assert PF.discrimination({PF.OBSERVED: 12},
                             c["observed_share"])["verdict"] == "DISCRIMINATING"


def test_a_missing_control_is_unknown_never_discriminating():
    assert PF.discrimination({PF.OBSERVED: 12}, None)["verdict"] == "UNKNOWN"


def test_the_control_excludes_the_price_under_test():
    b = _book([(100.0, [(0.45, 1.0)])])
    c = PF.control(b, D("0.45"), until=120.0)
    assert c["prices_tested"] == 98          # the 1c grid minus the real one

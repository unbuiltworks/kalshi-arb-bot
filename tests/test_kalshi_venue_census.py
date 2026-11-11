from decimal import Decimal as D

from src import kalshi_venue_census as V


def test_the_census_is_exhaustive_and_says_how():
    c = V.CENSUS
    assert c["exhaustive"] is True
    assert c["pages"] == 917
    assert c["total_program_rows"] == 183_329
    assert c["active_series"] == 300


def test_the_earlier_truncation_is_recorded_not_hidden():
    """A 200-page cap returned 40,000 rows and would have supported a
    venue-wide claim it could not justify."""
    assert "capped at 200 pages" in V.CENSUS["method"]
    assert "NOT venue-complete" in V.CENSUS["method"]
    assert "before any ranking claim was made" in V.CENSUS["method"]


def test_density_normalises_by_period_length():
    """A $100 pool over a week is not a $100 pool over 15 minutes."""
    quarter_hour = V.density_per_hour(D(20), 300, 15)
    daily = V.density_per_hour(D(100), 1000, 1440)
    assert quarter_hour > daily
    assert quarter_hour == D("0.2667").quantize(D("0.0001")) or \
        abs(quarter_hour - D("0.2667")) < D("0.0001")


def test_density_is_zero_rather_than_dividing_by_zero():
    assert V.density_per_hour(D(20), 0, 15) == D(0)
    assert V.density_per_hour(D(20), 300, 0) == D(0)


def test_our_legs_hold_the_top_five_places():
    top5 = {t[0] for t in V.TOP[:5]}
    assert top5 == {"KXNATGAS15M", "KXCOPPER15M", "KXSILVER15M",
                    "KXWTI15M", "KXGOLD15M"}
    assert all(t[1] == D("0.2667") for t in V.TOP[:5])


def test_the_margin_over_the_venue_is_stated_in_both_directions():
    best = V.TOP[0][1]
    nxt = V.TOP[5][1]
    assert best / nxt > D("3.3")                       # over the next series
    assert best / V.VENUE_MEDIAN_DENSITY_PER_HOUR > D("400")   # over median


def test_the_biggest_pools_are_recorded_as_untradeable():
    b = V.BIG_POOLS_ARE_UNTRADEABLE
    assert b["KXAAAGASDIL"] == D("1.660")
    # every one of them costs more than par to manufacture a pair in
    assert all(v > D("1.00") for v in b.values())


def test_the_search_verdict_does_not_overclaim():
    assert "negative result about the venue's opportunity set" in V.SEARCH_VERDICT
    assert "not a positive one about our prospects" in V.SEARCH_VERDICT
    assert "share of score" in V.STILL_BINDING
    assert "binary rather than approximate" in V.STILL_BINDING

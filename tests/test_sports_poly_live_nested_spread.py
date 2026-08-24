from decimal import Decimal

from src.sports_poly_live_nested_spread import _is_full_game_spread, score_spread_pair
from src.strict_mlb_prop_snapshot import TopBook

D = Decimal


def _book(yes_ask: str, yes_depth: str, no_ask: str, no_depth: str) -> TopBook:
    return TopBook(D(yes_ask), D(yes_depth), D(no_ask), D(no_depth))


def test_nested_spread_selected_yes_orientation():
    # Same team: margin > -7.5 on low line (selected=YES), margin > -3.5 on high line.
    # Buy P(-7.5) + NOT P(-3.5); the second leg is NO when selected is YES.
    low = _book("0.40", "5", "0.61", "5")
    high = _book("0.30", "5", "0.71", "5")
    row = score_spread_pair(D("-7.5"), low, True, D("-3.5"), high, True)
    assert row is not None
    assert row["low_buy_side"] == "YES"
    assert row["high_buy_side"] == "NO"


def test_nested_spread_handles_selected_no_orientation():
    # If the team's proposition is encoded as market NO, buy NO at the low
    # threshold and YES (the opposite of selected NO) at the high threshold.
    low = _book("0.61", "5", "0.40", "5")
    high = _book("0.71", "5", "0.30", "5")
    row = score_spread_pair(D("-7.5"), low, False, D("-3.5"), high, False)
    assert row is not None
    assert row["low_buy_side"] == "NO"
    assert row["high_buy_side"] == "YES"


def test_nested_spread_requires_ordered_half_point_thresholds():
    book = _book("0.5", "5", "0.5", "5")
    assert score_spread_pair(D("-3.5"), book, True, D("-7.5"), book, True) is None
    assert score_spread_pair(D("-7"), book, True, D("-3"), book, True) is None


def test_nested_spread_requires_q1_depth_on_both_purchased_legs():
    low = _book("0.40", "0.5", "0.61", "5")
    high = _book("0.30", "5", "0.71", "5")
    assert score_spread_pair(D("-7.5"), low, True, D("-3.5"), high, True) is None


def test_spread_scope_rejects_segmented_markets():
    assert _is_full_game_spread({
        "sportsMarketTypeV2": "SPREAD",
        "title": "Seattle Seahawks -4.5",
        "slug": "asc-nfl-sea-ten-2026-08-23-neg-4pt5",
        "marketSides": [{"teamAbbreviation": "SEA"}, {"teamAbbreviation": "TEN"}],
    })
    assert not _is_full_game_spread({
        "sportsMarketTypeV2": "SPREAD",
        "title": "1st quarter Seattle Seahawks -1.5",
        "slug": "asc-nfl-sea-ten-2026-08-23-1q-neg-1pt5",
        "marketSides": [{"teamAbbreviation": "SEA"}, {"teamAbbreviation": "TEN"}],
    })

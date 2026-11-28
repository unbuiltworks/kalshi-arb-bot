"""line_spec / line_market_fair on the venue's real record shapes (28 Nov)."""
import time
from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from src import pmus_sports_sharp as SH
from src.pmus_sports_line_fair import line_market_fair, line_spec

NOW = time.time()


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


SIB = {"slug": "aec-nba-orl-was-2026-11-28",
       "sportsMarketType": "basketball_team_full_game_winner",
       "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
       "gameStartTime": _iso(NOW + 3600),
       "marketSides": [{"long": True, "team": {"name": "Orlando Magic"}},
                       {"long": False, "team": {"name": "Washington Wizards"}}]}
SPREAD = {"slug": "asc-nba-orl-was-2026-11-28-neg-3pt5",
          "sportsMarketType": "basketball_team_full_game_spread",
          "question": "Will the ORL Magic cover -3.5 vs the WAS Wizards?",
          "marketSides": [{"long": True, "description": "-3.50",
                           "team": {"name": "Orlando Magic"}},
                          {"long": False, "description": "+3.50"}]}
TOTAL = {"slug": "tsc-nba-orl-was-2026-11-28-221pt5",
         "sportsMarketType": "basketball_team_full_game_total",
         "question": "Will the total in ORL Magic vs. WAS Wizards be more "
                     "than 221.5?",
         "marketSides": [{"long": True, "description": "Over"},
                         {"long": False, "description": "Under"}]}


def _ev(spread_pt=-3.5, total_pt=221.5, age=30, book="pinnacle"):
    u = _iso(NOW - age)
    return [{"id": "e1", "home_team": "Washington Wizards",
             "away_team": "Orlando Magic", "commence_time": _iso(NOW + 3600),
             "bookmakers": [{"key": book, "markets": [
                 {"key": "spreads", "last_update": u, "outcomes": [
                     {"name": "Orlando Magic", "point": spread_pt,
                      "price": 1.8},
                     {"name": "Washington Wizards", "point": -spread_pt,
                      "price": 2.05}]},
                 {"key": "totals", "last_update": u, "outcomes": [
                     {"name": "Over", "point": total_pt, "price": 1.95},
                     {"name": "Under", "point": total_pt, "price": 1.87}]}]}]}]


def test_spread_priced_exactly():
    f, why = line_market_fair(SPREAD, SIB, _ev(), now=NOW)
    assert why == "ok" and f["kind"] == "spreads" and f["fair_yes"] > D("0.5")


def test_total_priced_exactly_yes_is_over():
    f, why = line_market_fair(TOTAL, SIB, _ev(), now=NOW)
    assert why == "ok" and f["fair_yes"] < D("0.5")      # Over at 1.95


def test_other_line_refused():
    assert line_market_fair(SPREAD, SIB, _ev(spread_pt=-4.5),
                            now=NOW)[1] == "line_not_offered_or_stale"
    assert line_market_fair(TOTAL, SIB, _ev(total_pt=222.5),
                            now=NOW)[1] == "line_not_offered_or_stale"


def test_stale_and_wrong_book_refused():
    assert line_market_fair(SPREAD, SIB, _ev(age=5000),
                            now=NOW)[0] is None
    assert line_market_fair(SPREAD, SIB, _ev(book="draftkings"),
                            now=NOW)[1] == "no_pinnacle"


def test_wrong_sibling_refused():
    sib = dict(SIB, slug="aec-nba-orl-was-2026-11-29")
    assert line_market_fair(SPREAD, sib, _ev(), now=NOW)[1] == \
        "sibling_mismatch"


@pytest.mark.parametrize("t", ["football_team_first_half_spread",
                               "football_team_points_full_game_total",
                               "basketball_team_total_points",
                               "hockey_team_first_period_total",
                               "basketball_player_points"])
def test_period_team_total_and_props_refused(t):
    spec, why = line_spec(dict(TOTAL, sportsMarketType=t))
    assert spec is None and why.startswith("unsupported_type")


def test_slug_description_disagreement_refused():
    bad = dict(SPREAD, marketSides=[{"long": True, "description": "-4.50",
                                     "team": {"name": "Orlando Magic"}}])
    assert line_spec(bad)[1] == "line_mismatch"
    badq = dict(TOTAL, question="Will the total be more than 220.5?")
    assert line_spec(badq)[1] == "line_mismatch"


def test_integer_line_refused_push_possible():
    m = dict(TOTAL, slug="tsc-nba-orl-was-2026-11-28-221pt0",
             question="Will the total in X be more than 221.0?")
    assert line_market_fair(m, SIB, _ev(total_pt=221.0), now=NOW)[0] is None


def test_unconfigured_sport_refused():
    m = dict(SPREAD, slug="asc-eurolg-efs-val-2026-12-03-neg-10pt5")
    assert line_spec(m)[1].startswith("sport_not_configured")

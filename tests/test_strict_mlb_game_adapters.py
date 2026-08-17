from decimal import Decimal

from src.strict_mlb_game_adapters import (
    GAME_FAMILY_SERIES,
    discover_game_candidates,
)


def _side(team: str, *, long: bool):
    return {
        "long": long,
        "description": team,
        "team": {"abbreviation": team, "name": team},
    }


def _poly_event(markets):
    return {
        "slug": "mlb-mia-phi-2026-08-17",
        "title": "MIA vs PHI",
        "startTime": "2026-08-17T18:40:00Z",
        "markets": markets,
    }


def _kalshi_event(series: str, market):
    return {
        "series_ticker": series,
        "event_ticker": f"{series}-26AUG171840MIAPHI",
        "title": "Miami at Philadelphia",
        "strike_date": "2026-08-17T18:40:00Z",
        "markets": [market],
    }


def test_moneyline_exact_team_matches_without_inversion():
    poly = _poly_event([
        {
            "slug": "mlb-mia-phi-ml-phi",
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
            "marketSides": [_side("PHI", long=True), _side("MIA", long=False)],
            "gameStartTime": "2026-08-17T18:40:00Z",
        }
    ])
    kalshi = _kalshi_event(
        GAME_FAMILY_SERIES["baseball_game_moneyline"],
        {
            "ticker": "KXMLBGAME-26AUG171840MIAPHI-PHI",
            "yes_sub_title": "Philadelphia Phillies",
            "close_time": "2026-08-17T18:40:00Z",
        },
    )
    rows = discover_game_candidates([poly], [kalshi])
    assert len(rows) == 1
    assert rows[0].predicate == "PHI_WIN"
    assert rows[0].poly_invert is False


def test_moneyline_short_team_is_aligned_by_inverting_poly_book():
    poly = _poly_event([
        {
            "slug": "mlb-mia-phi-ml-mia",
            "sportsMarketTypeV2": "MONEYLINE",
            "marketSides": [_side("MIA", long=True), _side("PHI", long=False)],
            "gameStartTime": "2026-08-17T18:40:00Z",
        }
    ])
    kalshi = _kalshi_event(
        GAME_FAMILY_SERIES["baseball_game_moneyline"],
        {
            "ticker": "KXMLBGAME-26AUG171840MIAPHI-PHI",
            "title": "Philadelphia Phillies",
            "close_time": "2026-08-17T18:40:00Z",
        },
    )
    rows = discover_game_candidates([poly], [kalshi])
    assert len(rows) == 1
    assert rows[0].poly_invert is True


def test_negative_half_run_spread_matches_same_team():
    poly = _poly_event([
        {
            "slug": "mlb-mia-phi-spread-phi-1pt5",
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_SPREAD",
            "line": -1.5,
            "marketSides": [_side("PHI", long=True), _side("MIA", long=False)],
            "gameStartTime": "2026-08-17T18:40:00Z",
        }
    ])
    kalshi = _kalshi_event(
        GAME_FAMILY_SERIES["baseball_game_spread"],
        {
            "ticker": "KXMLBSPREAD-26AUG171840MIAPHI-PHI15",
            "yes_sub_title": "Philadelphia Phillies wins by over 1.5 runs",
            "close_time": "2026-08-17T18:40:00Z",
        },
    )
    rows = discover_game_candidates([poly], [kalshi])
    assert len(rows) == 1
    assert rows[0].line == Decimal("1.5")
    assert rows[0].predicate == "PHI_MARGIN_GT_1.5"
    assert rows[0].poly_invert is False


def test_positive_opponent_spread_matches_by_inversion():
    poly = _poly_event([
        {
            "slug": "mlb-mia-phi-spread-mia-pos1pt5",
            "sportsMarketTypeV2": "SPREAD",
            "line": 1.5,
            "marketSides": [_side("MIA", long=True), _side("PHI", long=False)],
            "gameStartTime": "2026-08-17T18:40:00Z",
        }
    ])
    kalshi = _kalshi_event(
        GAME_FAMILY_SERIES["baseball_game_spread"],
        {
            "ticker": "KXMLBSPREAD-26AUG171840MIAPHI-PHI15",
            "title": "Philadelphia Phillies wins by over 1.5 runs",
            "close_time": "2026-08-17T18:40:00Z",
        },
    )
    rows = discover_game_candidates([poly], [kalshi])
    assert len(rows) == 1
    assert rows[0].poly_invert is True


def test_total_over_half_run_matches():
    poly = _poly_event([
        {
            "slug": "mlb-mia-phi-total-8pt5",
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_TOTAL",
            "line": 8.5,
            "question": "Will the game total be over 8.5 runs?",
            "marketSides": [{"long": True, "description": "Over"}, {"long": False, "description": "Under"}],
            "gameStartTime": "2026-08-17T18:40:00Z",
        }
    ])
    kalshi = _kalshi_event(
        GAME_FAMILY_SERIES["baseball_game_total"],
        {
            "ticker": "KXMLBTOTAL-26AUG171840MIAPHI-85",
            "yes_sub_title": "Over 8.5 runs scored",
            "close_time": "2026-08-17T18:40:00Z",
        },
    )
    rows = discover_game_candidates([poly], [kalshi])
    assert len(rows) == 1
    assert rows[0].predicate == "TOTAL_RUNS_GT_8.5"
    assert rows[0].poly_invert is False


def test_integer_spread_and_total_are_rejected_to_avoid_push_semantics():
    poly = _poly_event([
        {
            "slug": "mlb-mia-phi-spread-phi-2",
            "sportsMarketTypeV2": "SPREAD",
            "line": -2,
            "marketSides": [_side("PHI", long=True), _side("MIA", long=False)],
            "gameStartTime": "2026-08-17T18:40:00Z",
        },
        {
            "slug": "mlb-mia-phi-total-8",
            "sportsMarketTypeV2": "TOTAL",
            "line": 8,
            "question": "Will the game total be over 8 runs?",
            "marketSides": [{"long": True, "description": "Over"}],
            "gameStartTime": "2026-08-17T18:40:00Z",
        },
    ])
    spread = _kalshi_event(
        GAME_FAMILY_SERIES["baseball_game_spread"],
        {
            "ticker": "KXMLBSPREAD-26AUG171840MIAPHI-PHI2",
            "title": "Philadelphia Phillies wins by over 2 runs",
            "close_time": "2026-08-17T18:40:00Z",
        },
    )
    total = _kalshi_event(
        GAME_FAMILY_SERIES["baseball_game_total"],
        {
            "ticker": "KXMLBTOTAL-26AUG171840MIAPHI-8",
            "title": "Over 8 runs scored",
            "close_time": "2026-08-17T18:40:00Z",
        },
    )
    assert discover_game_candidates([poly], [spread, total]) == []


def test_wrong_game_time_fails_closed():
    poly = _poly_event([
        {
            "slug": "mlb-mia-phi-ml-phi",
            "sportsMarketTypeV2": "MONEYLINE",
            "marketSides": [_side("PHI", long=True), _side("MIA", long=False)],
            "gameStartTime": "2026-08-17T18:40:00Z",
        }
    ])
    kalshi = _kalshi_event(
        GAME_FAMILY_SERIES["baseball_game_moneyline"],
        {
            "ticker": "KXMLBGAME-26AUG171840MIAPHI-PHI",
            "yes_sub_title": "Philadelphia Phillies",
            "close_time": "2026-08-17T22:40:00Z",
        },
    )
    # event strike_date is still 18:40, so remove it to exercise market-time guard.
    kalshi.pop("strike_date")
    assert discover_game_candidates([poly], [kalshi], max_start_skew_seconds=60) == []

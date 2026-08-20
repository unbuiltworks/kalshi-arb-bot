from src.nfl_identity_probe import (
    canonical_team,
    normalize_gemini,
    normalize_kalshi,
    normalize_sportsbooks,
    summarize,
)


def test_nfl_aliases_are_sport_specific_not_mlb_aliases():
    assert canonical_team("Arizona Cardinals") == "ARI"
    assert canonical_team("ARI") == "ARI"
    assert canonical_team("JAC Jaguars") == "JAX"
    assert canonical_team("Washington Commanders") == "WAS"


def test_moneyline_same_game_joins_at_zero_start_skew():
    gemini = normalize_gemini([{
        "ticker": "NFL-2608212300-NYJ-PIT-M",
        "contracts": [
            {"instrumentSymbol": "GEMI-NFL-2608212300-NYJ-PIT-M-NYJ", "ticker": "NYJ", "label": "New York J"},
            {"instrumentSymbol": "GEMI-NFL-2608212300-NYJ-PIT-M-PIT", "ticker": "PIT", "label": "Pittsburgh"},
        ],
    }])
    kalshi = normalize_kalshi([{
        "event_ticker": "KXNFLGAME-26AUG21NYJPIT",
        "series_ticker": "KXNFLGAME",
        "markets": [
            {"ticker": "KXNFLGAME-26AUG21NYJPIT-NYJ", "yes_sub_title": "NY Jets", "title": "New York vs Pittsburgh: NY Jets wins?", "expected_expiration_time": "2026-08-22T02:00:00Z"},
            {"ticker": "KXNFLGAME-26AUG21NYJPIT-PIT", "yes_sub_title": "PIT Steelers", "title": "New York vs Pittsburgh: PIT Steelers wins?", "expected_expiration_time": "2026-08-22T02:00:00Z"},
        ],
    }])
    books = normalize_sportsbooks([{
        "id": "game1", "away_team": "New York Jets", "home_team": "Pittsburgh Steelers", "commence_time": "2026-08-21T23:00:00Z",
        "bookmakers": [{"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [{"name": "New York Jets", "price": 2.2}, {"name": "Pittsburgh Steelers", "price": 1.7}]}]}],
    }])
    result = summarize(gemini, kalshi, books, {})
    assert result["clusters_within_5m"] == 2
    assert result["exact_by_family"] == {"moneyline": 2}
    assert result["kalshi_start_hypothesis"]["zero_skew"] == 2
    assert result["kalshi_start_hypothesis"]["max_skew_ms"] == 0


def test_spread_and_total_parse_same_propositions():
    gemini = normalize_gemini([
        {
            "ticker": "NFL-2609100020-NE-SEA-S",
            "contracts": [{"instrumentSymbol": "GEMI-NFL-2609100020-NE-SEA-S-SEA2", "ticker": "SEA2", "label": "Seattle wins by more than 2.5 points"}],
        },
        {
            "ticker": "NFL-2609100020-NE-SEA-T",
            "contracts": [{"instrumentSymbol": "GEMI-NFL-2609100020-NE-SEA-T-O44", "ticker": "O44", "label": "More than 44.5 points scored"}],
        },
    ])
    kalshi = normalize_kalshi([
        {
            "event_ticker": "KXNFLSPREAD-26SEP09NESEA",
            "markets": [{"ticker": "KXNFLSPREAD-26SEP09NESEA-SEA2", "title": "Seattle wins by over 2.5 points", "yes_sub_title": "SEA Seahawks", "expected_expiration_time": "2026-09-10T03:20:00Z"}],
        },
        {
            "event_ticker": "KXNFLTOTAL-26SEP09NESEA",
            "markets": [{"ticker": "KXNFLTOTAL-26SEP09NESEA-45", "title": "Over 44.5 points scored", "yes_sub_title": "Over 44.5", "expected_expiration_time": "2026-09-10T03:20:00Z"}],
        },
    ])
    books = normalize_sportsbooks([{
        "id": "game2", "away_team": "New England Patriots", "home_team": "Seattle Seahawks", "commence_time": "2026-09-10T00:20:00Z",
        "bookmakers": [{"key": "draftkings", "markets": [
            {"key": "spreads", "outcomes": [{"name": "Seattle Seahawks", "point": -2.5, "price": 1.91}, {"name": "New England Patriots", "point": 2.5, "price": 1.91}]},
            {"key": "totals", "outcomes": [{"name": "Over", "point": 44.5, "price": 1.91}, {"name": "Under", "point": 44.5, "price": 1.91}]},
        ]}],
    }])
    result = summarize(gemini, kalshi, books, {})
    assert result["clusters_within_5m"] >= 2
    assert result["exact_by_family"]["spread"] >= 1
    assert result["exact_by_family"]["game_total"] >= 1

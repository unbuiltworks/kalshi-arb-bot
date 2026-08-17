from src.universal_three_venue_sports import summarize


def _team(abbr, name):
    return {"abbreviation": abbr, "name": name}


def _side(long, abbr, name):
    return {"long": long, "description": name, "team": _team(abbr, name)}


def fixtures():
    gemini = [
        {
            "ticker": "GEMI-MLB-2608171840-MIA-PHI-M",
            "sportsMarket": {"sport": "baseball", "type": "moneyline", "subject": "contest", "scope": {"type": "full_contest"}},
            "contracts": [
                {"instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-M-MIA", "label": "Miami"},
                {"instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-M-PHI", "label": "Philadelphia"},
            ],
        },
        {
            "ticker": "GEMI-MLB-2608171840-MIA-PHI-S",
            "sportsMarket": {"sport": "baseball", "type": "spread", "subject": "team", "scope": {"type": "full_contest"}, "metric": "runs"},
            "contracts": [
                {"instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-S-PHI", "label": "PHI -1.5", "strike": {"type": "spread", "value": "-1.5"}},
                {"instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-S-MIA", "label": "MIA +1.5", "strike": {"type": "spread", "value": "1.5"}},
            ],
        },
        {
            "ticker": "GEMI-MLB-2608171840-MIA-PHI-T",
            "sportsMarket": {"sport": "baseball", "type": "total", "subject": "contest", "scope": {"type": "full_contest"}, "metric": "runs"},
            "contracts": [
                {"instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-T-O", "label": "Over 8.5", "strike": {"type": "over", "value": "8.5"}},
                {"instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-T-U", "label": "Under 8.5", "strike": {"type": "under", "value": "8.5"}},
            ],
        },
    ]

    poly = [{
        "startTime": "2026-08-17T18:40:00Z",
        "participants": [{"team": _team("MIA", "Miami Marlins")}, {"team": _team("PHI", "Philadelphia Phillies")}],
        "markets": [
            {
                "slug": "mia-phi-moneyline",
                "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
                "question": "Will Philadelphia win?",
                "gameStartTime": "2026-08-17T18:40:00Z",
                "marketSides": [_side(True, "PHI", "Philadelphia Phillies"), _side(False, "MIA", "Miami Marlins")],
            },
            {
                "slug": "mia-phi-spread",
                "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_SPREAD",
                "question": "Philadelphia -1.5",
                "line": -1.5,
                "gameStartTime": "2026-08-17T18:40:00Z",
                "marketSides": [_side(True, "PHI", "Philadelphia Phillies"), _side(False, "MIA", "Miami Marlins")],
            },
            {
                "slug": "mia-phi-total",
                "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_TOTAL",
                "question": "Over 8.5 runs",
                "line": 8.5,
                "gameStartTime": "2026-08-17T18:40:00Z",
                "marketSides": [{"long": True, "description": "Over"}, {"long": False, "description": "Under"}],
            },
        ],
    }]

    kalshi = [
        {
            "series_ticker": "KXMLBGAME",
            "event_ticker": "KXMLBGAME-26AUG171840MIAPHI",
            "title": "Miami vs Philadelphia",
            "markets": [{"ticker": "KXMLBGAME-X-PHI", "title": "Philadelphia"}],
        },
        {
            "series_ticker": "KXMLBSPREAD",
            "event_ticker": "KXMLBSPREAD-26AUG171840MIAPHI",
            "title": "Miami vs Philadelphia",
            "markets": [{"ticker": "KXMLBSPREAD-X-PHI15", "title": "Philadelphia wins by over 1.5 runs"}],
        },
        {
            "series_ticker": "KXMLBTOTAL",
            "event_ticker": "KXMLBTOTAL-26AUG171840MIAPHI",
            "title": "Miami vs Philadelphia",
            "markets": [{"ticker": "KXMLBTOTAL-X-O85", "title": "Over 8.5 runs scored"}],
        },
    ]
    return gemini, poly, kalshi


def test_three_venue_game_lines_match_both_outcomes():
    result = summarize(*fixtures())
    assert result["three_venue_clusters"] == 6
    assert result["clusters_by_family"] == {"moneyline": 2, "spread": 2, "game_total": 2}
    assert result["execution_eligible_clusters"] == 0
    assert all(row["venue_count"] == 3 for row in result["top_three_venue"])
    assert {tuple(row["venues"]) for row in result["top_three_venue"]} == {("gemini", "kalshi", "polymarket")}


def test_integer_line_does_not_create_push_ambiguous_cross_venue_cluster():
    gemini, poly, kalshi = fixtures()
    gemini[2]["contracts"][0]["strike"]["value"] = "8"
    gemini[2]["contracts"][1]["strike"]["value"] = "8"
    poly[0]["markets"][2]["line"] = 8
    kalshi[2]["markets"][0]["title"] = "Over 8 runs scored"
    result = summarize(gemini, poly, kalshi)
    assert result["three_venue_clusters"] == 4
    assert result["clusters_by_family"] == {"moneyline": 2, "spread": 2}

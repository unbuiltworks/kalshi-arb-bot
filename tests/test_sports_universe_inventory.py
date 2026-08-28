from src.sports_universe_inventory import summarize_kalshi, summarize_poly


def test_summarize_poly_counts_market_taxonomy_and_samples():
    rows = [
        {
            "category": "sports",
            "marketType": "player_props",
            "sportsMarketType": "baseball_player_total_bases",
            "slug": "a",
            "question": "Player 2+ total bases?",
            "line": 2,
            "subcategory": "mlb",
        },
        {
            "category": "sports",
            "marketType": "player_props",
            "sportsMarketType": "baseball_player_total_bases",
            "slug": "b",
            "question": "Player 3+ total bases?",
            "line": 3,
            "subcategory": "mlb",
        },
    ]
    result = summarize_poly(rows)
    assert result["markets"] == 2
    assert result["market_types"]["player_props"] == 2
    assert result["sports_market_types"]["baseball_player_total_bases"] == 2
    assert len(result["samples_by_sports_market_type"]["baseball_player_total_bases"]) == 2


def test_summarize_kalshi_counts_series_and_strikes():
    events = [
        {
            "category": "Sports",
            "series_ticker": "KXMLBPLAYERBASES",
            "title": "Phillies at Mets",
            "markets": [
                {"ticker": "K1", "title": "Harper total bases", "yes_sub_title": "2 or more", "strike_type": "greater"},
                {"ticker": "K2", "title": "Schwarber total bases", "yes_sub_title": "2 or more", "strike_type": "greater"},
            ],
        }
    ]
    result = summarize_kalshi(events)
    assert result["events"] == 1
    assert result["markets"] == 2
    assert result["series_tickers"]["KXMLBPLAYERBASES"] == 2
    assert result["strike_types"]["greater"] == 2

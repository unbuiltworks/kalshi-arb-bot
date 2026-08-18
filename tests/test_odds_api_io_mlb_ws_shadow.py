from src.odds_api_io_mlb_ws_shadow import _market_summary, _mlb_league_slug, _names


def test_names_extracts_selected_bookmakers():
    payload = {"bookmakers": [{"name": "DraftKings"}, {"name": "FanDuel"}]}
    assert _names(payload) == {"DraftKings", "FanDuel"}


def test_mlb_league_slug_finds_mlb():
    events = [
        {"league": {"name": "KBO", "slug": "korea-kbo"}},
        {"league": {"name": "Major League Baseball", "slug": "usa-mlb"}},
    ]
    assert _mlb_league_slug(events) == "usa-mlb"


def test_market_summary_does_not_include_raw_odds_payload():
    message = {
        "markets": [
            {
                "name": "ML",
                "updatedAt": "2099-01-01T00:00:00Z",
                "odds": [{"home": "1.90", "away": "2.00"}],
            }
        ]
    }
    summary = _market_summary(message)
    assert summary[0]["name"] == "ML"
    assert summary[0]["odds_rows"] == 1
    assert "odds" not in summary[0]

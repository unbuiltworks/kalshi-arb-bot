from decimal import Decimal

import src.allbooks_kalshi_mlb_prop_multi_shadow as scan

D = Decimal


def test_allbooks_parser_preserves_books_and_best_price_per_book():
    body = {
        "id":"game",
        "home_team":"Cincinnati Reds",
        "away_team":"St. Louis Cardinals",
        "commence_time":"2026-08-20T16:41:00Z",
        "bookmakers":[
            {"key":"fanduel","markets":[{"key":"pitcher_strikeouts","last_update":"2026-08-20T13:00:00Z","outcomes":[
                {"name":"Over","description":"Brady Singer","point":4.5,"price":2.00},
            ]},{"key":"pitcher_strikeouts_alternate","last_update":"2026-08-20T13:00:01Z","outcomes":[
                {"name":"Over","description":"Brady Singer","point":4.5,"price":2.10},
            ]}]},
            {"key":"draftkings","markets":[{"key":"pitcher_strikeouts_alternate","last_update":"2026-08-20T13:00:02Z","outcomes":[
                {"name":"Over","description":"Brady Singer","point":4.5,"price":2.25},
            ]}]},
        ],
    }
    rows = scan._sportsbook_legs(body)
    assert len(rows) == 2
    by_book = {row["book"]: row for row in rows}
    assert by_book["fanduel"]["decimal_odds"] == D("2.1")
    assert by_book["draftkings"]["decimal_odds"] == D("2.25")
    assert by_book["fanduel"]["threshold"] == D("5")
    assert by_book["draftkings"]["threshold"] == D("5")


def test_plan_retains_sportsbook_identity_and_uses_opposite_kalshi_side():
    sb = {
        "book":"draftkings", "teams":("CIN","STL"), "start_ms":1,
        "family":"baseball_player_strikeouts", "stat":"strikeouts",
        "player":"Brady Singer", "player_key":"bradysinger", "threshold":D("5"),
        "side":"YES", "decimal_odds":D("2.5"), "unit_stake":D("0.4"),
        "quote_ms":100, "market_key":"pitcher_strikeouts_alternate",
    }
    book = type("Book", (), {"yes_ask":D("0.70"),"yes_depth":D("10"),"no_ask":D("0.55"),"no_depth":D("10")})()
    k = {"teams":("CIN","STL"),"start_ms":1,"family":"baseball_player_strikeouts","player_key":"bradysinger","threshold":D("5"),"ticker":"KTEST","book":book}
    row = scan.best_plan(sb, k, multiplier=D("0.5"), snapshot_ms=100)
    assert row is not None
    assert row["sportsbook"] == "draftkings"
    assert row["kalshi_side"] == "NO"
    assert row["settlement_status"].startswith("NOT_EXECUTABLE")


def test_family_mismatch_fails_closed():
    sb = {"book":"fanduel","teams":("CIN","STL"),"start_ms":1,"family":"baseball_player_hits","stat":"hits","player":"X","player_key":"x","threshold":D("1"),"side":"YES","decimal_odds":D("2"),"unit_stake":D("0.5"),"quote_ms":1,"market_key":"batter_hits_alternate"}
    k = {"teams":("CIN","STL"),"start_ms":1,"family":"baseball_player_home_runs","player_key":"x","threshold":D("1"),"ticker":"K","book":object()}
    assert scan.best_plan(sb, k, multiplier=D("0.5"), snapshot_ms=1) is None

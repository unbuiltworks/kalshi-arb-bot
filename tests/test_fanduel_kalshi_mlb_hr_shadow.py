from decimal import Decimal

import src.fanduel_kalshi_mlb_hr_shadow_v2 as hr

D = Decimal


def _kalshi_event():
    market = {
        "ticker": "KXMLBHR-26AUG201240STLCIN-CINEDELACRUZ44-1",
        "title": "Elly De La Cruz: 1+ home runs?",
        "yes_ask_dollars": "0.30",
        "yes_ask_size_fp": "5",
        "no_ask_dollars": "0.72",
        "no_ask_size_fp": "5",
    }
    return {
        "event_ticker": "KXMLBHR-26AUG201240STLCIN",
        "title": "St. Louis vs Cincinnati: Home Runs",
        "markets": [market],
    }, market


def _fanduel_body():
    return {
        "id": "odds-game",
        "home_team": "Cincinnati Reds",
        "away_team": "St. Louis Cardinals",
        "commence_time": "2026-08-20T16:41:00Z",
        "bookmakers": [{
            "key": "fanduel",
            "markets": [{
                "key": "batter_home_runs_alternate",
                "last_update": "2026-08-20T13:10:00Z",
                "outcomes": [{
                    "name": "Over",
                    "description": "Elly De La Cruz",
                    "point": 0.5,
                    "price": 5.0,
                }],
            }],
        }],
    }


def test_kalshi_hr_identity_parses_exact_game_player_threshold():
    event, market = _kalshi_event()
    row = hr._kalshi_market_identity(event, market)
    assert row is not None
    assert row["teams"] == ("CIN", "STL")
    assert row["player_key"] == "ellydelacruz"
    assert row["threshold"] == D("1")
    assert row["start_ms"] == 1787244000000


def test_fanduel_half_point_hr_normalizes_to_integer_yes_threshold():
    rows = hr._fanduel_legs(_fanduel_body())
    assert len(rows) == 1
    row = rows[0]
    assert row["teams"] == ("CIN", "STL")
    assert row["player_key"] == "ellydelacruz"
    assert row["threshold"] == D("1")
    assert row["side"] == "YES"
    assert row["cost"] == D("0.2")


def test_exact_hr_pair_uses_opposite_kalshi_side_and_fee():
    event, market = _kalshi_event()
    kid = hr._kalshi_market_identity(event, market)
    fd = hr._fanduel_legs(_fanduel_body())[0]
    assert kid is not None
    row = hr.evaluate_pair(fd, kid, market, multiplier=D("1"), snapshot_ms=1787221800000)
    assert row is not None
    assert row["kalshi_side"] == "NO"
    assert D(row["kalshi_fee_upper_bound"]) > 0
    assert D(row["shadow_profit_per_dollar"]) > 0
    assert row["settlement_status"].startswith("NOT_EXECUTABLE")


def test_hr_pair_fails_closed_on_player_or_threshold_mismatch():
    event, market = _kalshi_event()
    kid = hr._kalshi_market_identity(event, market)
    assert kid is not None
    fd = dict(hr._fanduel_legs(_fanduel_body())[0])
    fd["player_key"] = "differentplayer"
    assert hr.evaluate_pair(fd, kid, market, multiplier=D("1"), snapshot_ms=0) is None

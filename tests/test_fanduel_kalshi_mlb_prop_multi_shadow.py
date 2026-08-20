from decimal import Decimal

import src.fanduel_kalshi_mlb_prop_multi_shadow as scan

D = Decimal


def _event(series, title, event_ticker, market):
    return {
        "series_ticker": series,
        "title": title,
        "event_ticker": event_ticker,
        "markets": [market],
    }


def test_kalshi_hit_and_hr_markets_parse_to_distinct_exact_families():
    hit_market = {
        "ticker": "KXMLBHIT-26AUG201240STLCIN-CINEDELACRUZ44-1",
        "title": "Elly De La Cruz: 1+ hits?",
        "yes_sub_title": "Elly De La Cruz: 1+ hits?",
        "yes_ask_dollars": "0.55", "yes_ask_size_fp": "10",
        "no_ask_dollars": "0.47", "no_ask_size_fp": "10",
    }
    hit = scan._kalshi_row(_event(
        "KXMLBHIT", "St. Louis vs Cincinnati: Hits",
        "KXMLBHIT-26AUG201240STLCIN", hit_market,
    ), hit_market)
    assert hit is not None
    assert hit["family"] == "baseball_player_hits"
    assert hit["player_key"] == "ellydelacruz"
    assert hit["threshold"] == D("1")
    assert hit["teams"] == ("CIN", "STL")

    hr_market = {
        "ticker": "KXMLBHR-26AUG201240STLCIN-CINEDELACRUZ44-1",
        "title": "Elly De La Cruz: 1+ home runs?",
        "yes_ask_dollars": "0.20", "yes_ask_size_fp": "10",
        "no_ask_dollars": "0.82", "no_ask_size_fp": "10",
    }
    hr = scan._kalshi_row(_event(
        "KXMLBHR", "St. Louis vs Cincinnati: Home Runs",
        "KXMLBHR-26AUG201240STLCIN", hr_market,
    ), hr_market)
    assert hr is not None
    assert hr["family"] == "baseball_player_home_runs"
    assert hr["threshold"] == D("1")


def test_fanduel_parser_keeps_real_sides_and_dedupes_duplicate_strikeout_line():
    body = {
        "id": "game",
        "home_team": "Cincinnati Reds",
        "away_team": "St. Louis Cardinals",
        "commence_time": "2026-08-20T16:41:00Z",
        "bookmakers": [{"key": "fanduel", "markets": [
            {"key": "batter_hits_alternate", "last_update": "2026-08-20T13:00:00Z", "outcomes": [
                {"name": "Over", "description": "Elly De La Cruz", "point": 0.5, "price": 1.60},
                {"name": "Under", "description": "Elly De La Cruz", "point": 0.5, "price": 2.40},
            ]},
            {"key": "pitcher_strikeouts", "last_update": "2026-08-20T13:00:00Z", "outcomes": [
                {"name": "Over", "description": "Brady Singer", "point": 4.5, "price": 2.00},
            ]},
            {"key": "pitcher_strikeouts_alternate", "last_update": "2026-08-20T13:00:01Z", "outcomes": [
                {"name": "Over", "description": "Brady Singer", "point": 4.5, "price": 2.10},
            ]},
        ]}],
    }
    rows = scan._fanduel_legs(body)
    hits = [r for r in rows if r["family"] == "baseball_player_hits"]
    ks = [r for r in rows if r["family"] == "baseball_player_strikeouts"]
    assert {r["side"] for r in hits} == {"YES", "NO"}
    assert {r["threshold"] for r in hits} == {D("1")}
    assert len(ks) == 1
    assert ks[0]["decimal_odds"] == D("2.1")
    assert ks[0]["threshold"] == D("5")


def test_q5_can_turn_q1_fee_rounding_loss_into_positive_plan():
    # Prefee unit cost is 0.995. At q=1 the conservative Kalshi fee rounds to $0.01,
    # so the pair loses half a cent. At q=5 the total fee rounds to $0.02 and the
    # total capital becomes $4.995, producing a real shadow +$0.005 plan.
    fd = {
        "teams": ("CIN", "STL"), "start_ms": 1787244060000,
        "family": "baseball_player_hits", "stat": "hits",
        "player": "Test Player", "player_key": "testplayer", "threshold": D("1"),
        "side": "YES", "decimal_odds": D("1") / D("0.895"),
        "unit_stake": D("0.895"), "quote_ms": 1000, "market_key": "batter_hits_alternate",
    }
    book = type("Book", (), {
        "yes_ask": D("0.90"), "yes_depth": D("10"),
        "no_ask": D("0.10"), "no_depth": D("10"),
    })()
    k = {
        "teams": ("CIN", "STL"), "start_ms": 1787244060000,
        "family": "baseball_player_hits", "player_key": "testplayer",
        "threshold": D("1"), "ticker": "TEST", "book": book,
    }
    row = scan.best_plan(fd, k, multiplier=D("0.5"), snapshot_ms=1000)
    assert row is not None
    assert row["quantity"] == 5
    assert D(row["total_capital"]) == D("4.995")
    assert D(row["shadow_locked_profit"]) == D("0.005")


def test_best_plan_fails_closed_on_family_mismatch():
    fd = {
        "teams": ("CIN", "STL"), "start_ms": 1,
        "family": "baseball_player_hits", "stat": "hits", "player": "X",
        "player_key": "x", "threshold": D("1"), "side": "YES",
        "decimal_odds": D("2"), "unit_stake": D("0.5"), "quote_ms": 1,
        "market_key": "batter_hits_alternate",
    }
    k = {
        "teams": ("CIN", "STL"), "start_ms": 1,
        "family": "baseball_player_home_runs", "player_key": "x",
        "threshold": D("1"), "ticker": "X", "book": object(),
    }
    assert scan.best_plan(fd, k, multiplier=D("0.5"), snapshot_ms=1) is None

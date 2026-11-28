"""SH.diagnose: why a market has no sportsbook fair (read-only)."""
import time
from datetime import datetime, timezone

from src import pmus_sports_sharp as SH

NOW = time.time()


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


MK = {"gameStartTime": _iso(NOW + 3600), "marketSides": [
    {"long": True, "team": {"name": "Los Angeles Lakers"}},
    {"long": False, "team": {"name": "San Antonio Spurs"}}]}


def _ev(home="Los Angeles Lakers", away="San Antonio Spurs", age=60,
        book="pinnacle", start=NOW + 3600):
    return {"home_team": home, "away_team": away,
            "commence_time": _iso(start),
            "bookmakers": [{"key": book, "markets": [{
                "key": "h2h", "last_update": _iso(NOW - age),
                "outcomes": [{"name": home, "price": 1.8},
                             {"name": away, "price": 2.1}]}]}]}


def test_ok_with_pinnacle():
    d = SH.diagnose(MK, [_ev()], now=NOW)
    assert d["why"] == "ok" and d["pinnacle"] and not d["live"]


def test_alias_mismatch_reports_nearest():
    d = SH.diagnose(MK, [_ev(home="LA Lakers")], now=NOW)
    assert d["why"] == "no_exact_team_match"
    assert d["nearest"] == ["LA Lakers", "San Antonio Spurs"]


def test_stale_and_no_pinnacle():
    d = SH.diagnose(MK, [_ev(age=5000, book="betfair_ex_eu")], now=NOW)
    assert d["why"] == "odds_stale" and d["pinnacle"] is False


def test_empty_feed_and_time_mismatch():
    assert SH.diagnose(MK, [], now=NOW)["why"] == "feed_empty"
    assert SH.diagnose(MK, [_ev(start=NOW + 90000)],
                       now=NOW)["why"] == "start_time_mismatch"


def test_never_raises():
    assert SH.diagnose({"marketSides": None}, [None, 3],
                       now=NOW)["why"] == "market_teams_unreadable"

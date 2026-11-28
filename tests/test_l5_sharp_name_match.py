"""PMUS_SHARP_NAME_MATCH=words: nickname markets (NHL on Polymarket US) match
full feed names one-to-one; the default stays exact."""
import time
from datetime import datetime, timezone

from src import pmus_sports_sharp as SH

NOW = time.time()


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _mk(y, n):
    return {"gameStartTime": _iso(NOW + 3600), "marketSides": [
        {"long": True, "team": {"name": y}},
        {"long": False, "team": {"name": n}}]}


def _ev(home, away, ph=1.8, pa=2.1):
    return {"id": "e1", "home_team": home, "away_team": away,
            "commence_time": _iso(NOW + 3600),
            "bookmakers": [{"key": "pinnacle", "markets": [{
                "key": "h2h", "last_update": _iso(NOW - 30),
                "outcomes": [{"name": home, "price": ph},
                             {"name": away, "price": pa}]}]}]}


EV = [_ev("Florida Panthers", "Tampa Bay Lightning")]


def test_default_exact_refuses_nicknames(monkeypatch):
    monkeypatch.setattr(SH, "NAME_MATCH", "exact")
    assert SH.fair_yes(_mk("Lightning", "Panthers"), EV, now=NOW) is None


def test_words_matches_nicknames_and_maps_yes(monkeypatch):
    monkeypatch.setattr(SH, "NAME_MATCH", "words")
    f = SH.fair_yes(_mk("Lightning", "Panthers"), EV, now=NOW)
    assert f is not None and f["source"] == "pinnacle"
    # YES = Lightning = away at 2.1 -> the lower probability
    assert f["fair_yes"] < 0.5


def test_words_accents_and_prefix(monkeypatch):
    monkeypatch.setattr(SH, "NAME_MATCH", "words")
    ev = [_ev("Montreal Canadiens", "Vegas Golden Knights")]
    assert SH.fair_yes(_mk("Canadiens", "Golden Knights"), ev,
                       now=NOW) is not None
    assert SH.name_match("Montréal Canadiens", "Montreal Canadiens")
    assert SH.name_match("Air Force", "Air Force Falcons", "words")


def test_words_refuses_ambiguous_and_partial_words(monkeypatch):
    monkeypatch.setattr(SH, "NAME_MATCH", "words")
    assert not SH.name_match("Kings", "Kingston", "words")
    # both market names fit the same feed team -> refused
    assert SH.teams_match(("Rangers", "New York Rangers"),
                          "New York Rangers", "Texas Rangers",
                          "words") is None


def test_full_names_unchanged_in_both_modes(monkeypatch):
    ev = [_ev("Washington Wizards", "Orlando Magic")]
    for mode in ("exact", "words"):
        monkeypatch.setattr(SH, "NAME_MATCH", mode)
        f = SH.fair_yes(_mk("Orlando Magic", "Washington Wizards"), ev,
                        now=NOW)
        assert f is not None and f["fair_yes"] < 0.5


class _R:
    def __init__(self, body=b"[]"):
        self.body, self.headers = body, {"x-requests-remaining": "200",
                                         "x-requests-used": "300"}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_on_demand_refresh_respects_age_and_daily_cap(monkeypatch):
    monkeypatch.setenv("PMUS_SHARP_REFRESH", "on_demand")
    monkeypatch.setenv("PMUS_SHARP_DAILY_CALLS", "2")
    clock = [1795900000.0]
    calls = []
    c = SH.OddsClient(key="k", opener=lambda u, timeout: (calls.append(u),
                                                          _R())[1],
                      clock=lambda: clock[0])
    c.fetch("basketball_nba")
    c.fetch("basketball_nba")                  # fresh cache: no call
    assert len(calls) == 1
    clock[0] += SH.MAX_ODDS_AGE_S + 1
    c.fetch("basketball_nba")
    clock[0] += SH.MAX_ODDS_AGE_S + 1
    c.fetch("basketball_nba")                  # over the daily cap
    assert len(calls) == 2 and c.last_error == "DAILY_CALL_CAP"
    assert c.status()["calls_today"] == 2


def test_default_poll_unchanged(monkeypatch):
    monkeypatch.delenv("PMUS_SHARP_REFRESH", raising=False)
    clock = [1795900000.0]
    calls = []
    c = SH.OddsClient(key="k", poll_s=3600, opener=lambda u, timeout: (
        calls.append(u), _R())[1], clock=lambda: clock[0])
    c.fetch("basketball_nba")
    clock[0] += SH.MAX_ODDS_AGE_S + 1
    c.fetch("basketball_nba")
    assert len(calls) == 1

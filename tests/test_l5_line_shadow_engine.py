"""Engine wiring: sharp_line_shadow prices exact full-game lines from the
already-fetched odds into the shadow state; places nothing; the slate gets
priced line markets only with PMUS_RN1_LINE_MARKETS."""
import time
from datetime import datetime, timezone
from decimal import Decimal as D

from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import eng  # noqa: F401


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _setup(e, v):
    now = time.time()
    start = _iso(now + 3 * 3600)
    sib = {"slug": "aec-nba-orl-was-2026-11-28", "gameStartTime": start,
           "sportsMarketType": "basketball_team_full_game_winner",
           "marketSides": [{"long": True, "team": {"name": "Orlando Magic"}},
                           {"long": False,
                            "team": {"name": "Washington Wizards"}}]}
    line = {"slug": "asc-nba-orl-was-2026-11-28-neg-3pt5",
            "sportsMarketType": "basketball_team_full_game_spread",
            "bestBidQuote": {"value": "0.50"},
            "bestAskQuote": {"value": "0.52"},
            "marketSides": [{"long": True, "description": "-3.50",
                             "team": {"name": "Orlando Magic"}}]}
    v.gget = lambda path: {"events": [{"markets": [sib]}]}
    v.bid, v.ask = D("0.53"), D("0.57")
    u = datetime.fromtimestamp(now - 60, timezone.utc).isoformat()
    ev = {"id": "e1", "commence_time": start,
          "home_team": "Washington Wizards", "away_team": "Orlando Magic",
          "bookmakers": [{"key": "pinnacle", "markets": [
              {"key": "h2h", "last_update": u, "outcomes": [
                  {"name": "Orlando Magic", "price": 1.6},
                  {"name": "Washington Wizards", "price": 2.4}]},
              {"key": "spreads", "last_update": u, "outcomes": [
                  {"name": "Orlando Magic", "point": -3.5, "price": 1.9},
                  {"name": "Washington Wizards", "point": 3.5,
                   "price": 1.95}]}]}]}

    class Odds:
        cache = {"basketball_nba": (now, [ev])}

        def fetch(self, key):
            return [ev]

        def status(self):
            return {"configured": True}

        def cache_age_s(self, key):
            return 0.0
    e._odds = Odds()
    e._line_cache = {"nba-orl-was-2026-11-28": {"t": now, "markets": [line],
                                                 "sibling": sib}}
    e._line_thread = object()             # no background thread in tests
    return now


def test_line_priced_into_shadow_state_and_nothing_placed(eng, monkeypatch,
                                                          capsys):
    e, v = eng
    monkeypatch.setattr(M, "SHARP_LINES", True)
    now = _setup(e, v)
    n = v.n
    e.sharp_shadow_tick(now)
    st = e.b.d["sharp_shadow"]["asc-nba-orl-was-2026-11-28-neg-3pt5"]
    assert st["kind"] == "spreads" and D(st["fair_yes"]) > D("0.5")
    assert st["sibling"] == "aec-nba-orl-was-2026-11-28"
    assert v.n == n
    assert "SHARP_LINES" in capsys.readouterr().out


def test_lines_off_by_default(eng, monkeypatch):
    e, v = eng
    now = _setup(e, v)
    e.sharp_shadow_tick(now)
    assert "asc-nba-orl-was-2026-11-28-neg-3pt5" not in \
        (e.b.d.get("sharp_shadow") or {})


def test_slate_gets_priced_lines_only_with_flag(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SHARP_LINES", True)
    now = _setup(e, v)
    e.sharp_shadow_tick(now)
    monkeypatch.setattr(e, "_slate_targets", lambda: [])
    monkeypatch.setattr(M, "RN1_LINE_MARKETS", False)
    assert e.slate_targets() == []
    monkeypatch.setattr(M, "RN1_LINE_MARKETS", True)
    assert [t["slug"] for t in e.slate_targets()] == \
        ["asc-nba-orl-was-2026-11-28-neg-3pt5"]

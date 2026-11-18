"""Pre-game sharp-odds mode: fair value, matching, quoting, residual, budget,
key hygiene and the shadow tick (owner 2026-11-18)."""
import io
import json
import time
import urllib.error
from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from src import pmus_sports_sharp as SH

NOW = datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc).timestamp()
START = "2026-11-19T00:00:00Z"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


MARKET = {"slug": "aec-nba-mil-mia-2026-11-18", "gameStartTime": START,
          "sportsMarketType": "basketball_team_full_game_winner",
          "marketSides": [
              {"long": True, "team": {"name": "Milwaukee Bucks"}},
              {"long": False, "team": {"name": "Miami Heat"}}]}


def event(pin=(1.80, 2.10), others=(), start=START, age=60):
    books = []
    if pin is not None:
        books.append({"key": "pinnacle", "markets": [{
            "key": "h2h", "last_update": iso(NOW - age),
            "outcomes": [{"name": "Milwaukee Bucks", "price": pin[0]},
                         {"name": "Miami Heat", "price": pin[1]}]}]})
    for k, (a, b) in others:
        books.append({"key": k, "markets": [{
            "key": "h2h", "last_update": iso(NOW - age),
            "outcomes": [{"name": "Milwaukee Bucks", "price": a},
                         {"name": "Miami Heat", "price": b}]}]})
    return {"id": "ev1", "commence_time": start, "home_team": "Miami Heat",
            "away_team": "Milwaukee Bucks", "bookmakers": books}


# ------------------------------------------------------------- fair value
def test_devig_removes_the_margin():
    p = SH.devig([1.80, 2.10])
    assert abs(sum(p) - 1) < D("1e-12")
    assert D("0.53") < p[0] < D("0.54")
    for bad in ([1.0, 2.0], [0, 2], ["x", 2], [2.0]):
        assert SH.devig(bad) is None


def test_pinnacle_preferred_then_median_and_staleness():
    f = SH.fair_from_event(event(others=[("bet365", (1.5, 2.6))]), now=NOW)
    assert f["source"] == "pinnacle"
    f = SH.fair_from_event(event(pin=None, others=[
        ("a", (1.80, 2.10)), ("b", (1.70, 2.25)), ("c", (1.90, 2.00))]),
        now=NOW)
    assert f["source"] == "median_of_3"
    assert abs(sum(f["probs"].values()) - 1) < D("1e-12")
    assert SH.fair_from_event(event(age=SH.MAX_ODDS_AGE_S + 1), now=NOW) \
        is None


def test_three_way_markets_are_refused():
    ev = event(pin=None)
    ev["bookmakers"] = [{"key": "pinnacle", "markets": [{
        "key": "h2h", "last_update": iso(NOW),
        "outcomes": [{"name": "A", "price": 2.5}, {"name": "B", "price": 3},
                     {"name": "Draw", "price": 3.2}]}]}]
    assert SH.fair_from_event(ev, now=NOW) is None


# --------------------------------------------------------------- matching
def test_yes_is_the_long_side_team_and_match_is_exact():
    assert SH.market_teams(MARKET) == ("Milwaukee Bucks", "Miami Heat")
    fy = SH.fair_yes(MARKET, [event()], now=NOW)
    p_mil = SH.devig([1.80, 2.10])[0]
    assert fy["fair_yes"] == p_mil and fy["source"] == "pinnacle"


def test_no_match_on_other_teams_far_start_or_ambiguity():
    other = dict(event(), home_team="Boston Celtics")
    assert SH.fair_yes(MARKET, [other], now=NOW) is None
    far = event(start=iso(NOW + 6 * 3600 + 3 * 3600))
    assert SH.fair_yes(MARKET, [far], now=NOW) is None
    assert SH.fair_yes(MARKET, [event(), dict(event(), id="ev2")],
                       now=NOW) is None                   # two candidates
    nosides = dict(MARKET, marketSides=[{"long": True, "team": {}}])
    assert SH.fair_yes(nosides, [event()], now=NOW) is None


def test_window_respects_cutoff_and_max_lead():
    assert SH.in_window(iso(NOW + 3600), NOW)
    assert not SH.in_window(iso(NOW + SH.CUTOFF_S - 1), NOW)    # too close
    assert not SH.in_window(iso(NOW + SH.MAX_LEAD_S + 1), NOW)  # too early
    assert not SH.in_window(None, NOW)


# ------------------------------------------------------------------ quote
def test_two_sided_quote_around_fair_with_margin():
    q = SH.quote("0.55", "0.53", "0.57", "0.01")
    assert q["mode"] == "two_sided"
    assert q["YES"] == D("0.52") and q["NO"] == D("0.42")
    assert q["pair"] == D("0.94")


def test_post_only_never_at_or_through_the_touch():
    q = SH.quote("0.55", "0.50", "0.51", "0.01")    # ask .51 < fair-margin
    assert q["YES"] is None or q["YES"] <= D("0.50")
    q = SH.quote("0.55", "0.54", "0.56", "0.01")
    assert q["YES"] <= D("0.55") and q["NO"] <= D("0.45")


def test_underpriced_side_is_quoted_alone():
    # sharp says YES 0.60; YES ask 0.55 is >= 2.5 points cheap
    q = SH.quote("0.60", "0.53", "0.55", "0.01")
    assert q["mode"] == "yes_only" and q["NO"] is None
    assert q["YES"] <= D("0.58") and q["YES"] <= D("0.54")
    # sharp says YES 0.40 -> NO fair 0.60; NO ask = 1 - yes_bid = 0.55
    q = SH.quote("0.40", "0.45", "0.47", "0.01")
    assert q["mode"] == "no_only" and q["YES"] is None
    assert q["NO"] <= D("0.54")


def test_bad_inputs_quote_nothing():
    for f, b, a in (("0", ".4", ".5"), ("1", ".4", ".5"), (".5", ".5", ".5")):
        assert SH.quote(f, b, a, "0.01")["mode"] == "none"


# --------------------------------------------------------------- residual
def test_residual_hold_complete_or_wait():
    assert SH.residual("0.50", "0.55", "0.60")["action"] == "HOLD"
    assert SH.residual("0.50", "0.505", "0.45")["action"] == "COMPLETE"
    w = SH.residual("0.50", "0.505", "0.52")
    assert w["action"] == "WAIT" and D(w["max_comp_px"]) == D("0.48")


# ------------------------------------------------------- client: budget/key
class _Resp(io.BytesIO):
    def __init__(self, body, rem="100", used="5"):
        super().__init__(json.dumps(body).encode())
        self.headers = {"x-requests-remaining": rem, "x-requests-used": used}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_client_caches_per_poll_and_tracks_budget():
    calls = []
    t = {"now": NOW}

    def opener(url, timeout):
        calls.append(url)
        return _Resp([event()], rem="80")
    c = SH.OddsClient(key="SECRETKEY", poll_s=3600, opener=opener,
                      clock=lambda: t["now"])
    assert len(c.fetch("basketball_nba")) == 1
    c.fetch("basketball_nba")
    assert len(calls) == 1                                  # cached
    t["now"] += 3601
    c.fetch("basketball_nba")
    assert len(calls) == 2 and c.remaining == 80


def test_client_stops_at_reserve_and_without_key():
    calls = []
    c = SH.OddsClient(key="K", poll_s=0, reserve=25,
                      opener=lambda u, timeout: calls.append(u) or
                      _Resp([], rem="25"))
    c.fetch("basketball_nba")
    c.fetch("basketball_nba")
    assert len(calls) == 1 and c.last_error == "BUDGET_RESERVE"
    c2 = SH.OddsClient(key="", opener=lambda u, timeout: 1 / 0)
    assert c2.fetch("basketball_nba") == [] and c2.last_error == "NO_KEY"


def test_key_never_appears_in_status_or_errors():
    def boom(url, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
    c = SH.OddsClient(key="SECRETKEY123", poll_s=0, opener=boom)
    c.fetch("basketball_nba")
    blob = json.dumps(c.status())
    assert "SECRETKEY123" not in blob and c.last_error == "HTTP_401"

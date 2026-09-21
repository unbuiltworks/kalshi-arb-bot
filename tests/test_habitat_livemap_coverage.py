"""Regression for the 2026-09-21 pmus_flow gap.

The liveness map used to be built by a SECOND pass over `/v2/leagues` (50
leagues) while the selection came from `/v2/sports` (266). An event in either
of the other 216 was simply absent from the map, and

    livemap.get(r["event"], (False,))[0]

reads absence as NOT LIVE -- so `pmus_flow_loop` selected nothing and wrote
nothing for 2h12m while every sibling thread stayed healthy. See
analysis/l5/PMUS_FLOW_INCIDENT_20260921.md.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import l5_live_habitat_service as S


# An overnight slate: the live event sits in a league that /v2/leagues omits.
SPORTS = {"sports": [{"leagues": [{"slug": "mlb"}, {"slug": "npb"}]}]}
EVENTS = {
    "mlb": {"events": [{"slug": "mlb-a-b", "eventState": {"live": False},
                        "startTime": "2030-01-01T00:00:00Z",
                        "markets": [{"slug": "mlb-a-b-ml",
                                     "marketType": "moneyline",
                                     "sportsMarketType": "moneyline",
                                     "bestBidQuote": "0.48",
                                     "bestAskQuote": "0.52"}]}]},
    "npb": {"events": [{"slug": "npb-hta-ybo", "eventState": {"live": True},
                        "startTime": "2026-09-21T04:00:00Z",
                        "markets": [{"slug": "npb-hta-ybo-ml",
                                     "marketType": "moneyline",
                                     "sportsMarketType": "moneyline",
                                     "bestBidQuote": "0.44",
                                     "bestAskQuote": "0.46"}]}]},
}


@pytest.fixture
def venue(monkeypatch):
    calls = []

    def fake_get(url, timeout=25.0):
        calls.append(url)
        if url.endswith("/v2/sports"):
            return SPORTS
        if url.endswith("/v2/leagues"):
            # The narrow list that caused the incident.
            return {"leagues": [{"slug": "mlb"}]}
        for lg, payload in EVENTS.items():
            if "/v2/leagues/%s/events" % lg in url:
                return payload
        raise AssertionError("unexpected URL %s" % url)

    monkeypatch.setattr(S, "get", fake_get)
    return calls


def test_discover_returns_a_livemap_covering_every_league_it_reached(venue):
    sel, lm = S.pmus_discover()
    assert set(lm) == {"mlb-a-b", "npb-hta-ybo"}
    assert lm["npb-hta-ybo"][0] is True
    assert lm["mlb-a-b"][0] is False


def test_the_narrow_v2_leagues_pass_is_gone(venue):
    S.pmus_discover()
    assert not [u for u in venue if u.endswith("/v2/leagues")], \
        "the 50-league list must no longer be a source of liveness"


def test_flow_loop_selects_the_live_event_that_used_to_be_invisible(venue):
    sel, lm = S.pmus_discover()
    state = {"pmus": sel, "pmus_live": lm}
    chosen = [r for r in state["pmus"]
              if state["pmus_live"].get(r["event"], (False,))[0]]
    assert [r["event"] for r in chosen] == ["npb-hta-ybo"]


def test_an_event_with_no_tradeable_market_is_still_answerable(monkeypatch):
    """It is dropped from the selection, but must not be MISSING from the map:
    absence and not-live are different facts to the selector."""
    def fake_get(url, timeout=25.0):
        if url.endswith("/v2/sports"):
            return {"sports": [{"leagues": [{"slug": "npb"}]}]}
        return {"events": [{"slug": "npb-x", "eventState": {"live": True},
                            "markets": [{"slug": "s", "bestBidQuote": None,
                                         "bestAskQuote": None}]}]}
    monkeypatch.setattr(S, "get", fake_get)
    sel, lm = S.pmus_discover()
    assert sel == []
    assert lm["npb-x"][0] is True


def test_a_failed_discovery_publishes_neither_half(monkeypatch):
    monkeypatch.setattr(S, "get", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    sel, lm = S.pmus_discover()
    assert sel == [] and lm == {}
    # discovery_loop's guard: a falsy selection leaves BOTH halves untouched,
    # so a good map can never be replaced by an empty one.
    state = {"pmus": ["kept"], "pmus_live": {"e": (True, None, None)}}
    if sel:
        state["pmus"], state["pmus_live"] = sel, lm
    assert state["pmus"] == ["kept"]
    assert state["pmus_live"] == {"e": (True, None, None)}


def test_selection_and_livemap_are_published_together(venue):
    """They were two independent `if x:` guards, so a stale selection could be
    judged by a fresh map and vice versa."""
    import inspect
    src = inspect.getsource(S.discovery_loop)
    assert "sel, lm = pmus_discover()" in src
    assert src.count('state["pmus_live"] = lm') == 1
    assert "if lm:" not in src


def test_heartbeat_distinguishes_empty_selection_from_stuck_writer():
    import inspect
    src = inspect.getsource(S.heartbeat_loop)
    assert "pmus_flow_selected" in src
    assert "pmus_live_events" in src
    flow = inspect.getsource(S.pmus_flow_loop)
    assert 'state["pmus_flow_selected"] = len(sel)' in flow


def test_nfl_pre_control_window_excludes_a_kickoff_beyond_12h():
    """The other half of the incident: the every-4th-sweep branch had nothing
    to select because the next kickoff (MNF) was ~20h out."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    soon = (now + dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    far = (now + dt.timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    past = (now - dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert S.kickoff_within_hours(soon, 12.0) is True
    assert S.kickoff_within_hours(far, 12.0) is False
    assert S.kickoff_within_hours(past, 12.0) is False


def test_the_gap_is_marked_in_the_stream_not_only_in_a_report():
    """An analysis that never opens analysis/ must still be unable to read that
    window as zero flow."""
    import inspect
    src = inspect.getsource(S.main)
    assert "PRESERVED_INCIDENT" in src
    assert "2026-09-21T03:55:13Z" in src and "2026-09-21T06:07:17Z" in src
    assert "CENSORED" in src
    assert "1220964" in src and "516596932" in src


def test_the_marker_names_the_cause_and_denies_the_writer_freeze():
    import inspect
    src = inspect.getsource(S.main)
    assert "not_a_writer_freeze" in src
    assert "/v2/leagues" in src and "/v2/sports" in src

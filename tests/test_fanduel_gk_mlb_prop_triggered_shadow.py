import asyncio
from types import SimpleNamespace

import src.fanduel_gk_mlb_prop_triggered_shadow as watch


def _side(teams=("cin", "stl"), start_ms=1787244060000):
    event = SimpleNamespace(participants=teams, scheduled_start_ms=start_ms)
    identity = SimpleNamespace(event=event)
    return SimpleNamespace(identity=identity)


def test_matching_odds_event_requires_exact_teams_and_start_envelope():
    sides = [_side()]
    rows = [
        {
            "id": "match",
            "home_team": "Cincinnati Reds",
            "away_team": "St. Louis Cardinals",
            "commence_time": "2026-08-20T16:41:00Z",
        },
        {
            "id": "wrong-game",
            "home_team": "Philadelphia Phillies",
            "away_team": "New York Mets",
            "commence_time": "2026-08-20T16:41:00Z",
        },
    ]
    matched = watch._matching_odds_events(sides, rows)
    assert [row["id"] for row in matched] == ["match"]


def test_matching_odds_event_rejects_start_outside_five_minutes():
    sides = [_side()]
    rows = [{
        "id": "late",
        "home_team": "Cincinnati Reds",
        "away_team": "St. Louis Cardinals",
        "commence_time": "2026-08-20T16:47:01Z",
    }]
    assert watch._matching_odds_events(sides, rows) == []


def test_zero_gk_inventory_returns_before_odds_api(monkeypatch):
    async def fake_discovery(http):
        del http
        return [], [], [], []

    async def fail_future(*args, **kwargs):
        raise AssertionError("Odds API must not be called without exact G/K joins")

    monkeypatch.setattr(watch, "discovery", fake_discovery)
    monkeypatch.setattr(watch, "_future_events", fail_future)
    assert asyncio.run(watch.trigger_batch("test-key")) is None

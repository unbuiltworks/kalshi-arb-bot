from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src import mlb_gk_final_lag_join_funnel as funnel


def _grow(*, stat="hits", subject="Jane Doe", teams=("AAA", "BBB"), start_ms=1_000_000, line=1.5):
    identity = SimpleNamespace(
        market_family="player_prop",
        stat=stat,
        subject=subject,
        event=SimpleNamespace(participants=teams, scheduled_start_ms=start_ms),
        comparator="GT",
        line=line,
    )
    return SimpleNamespace(identity=identity, venue_market_id="GEMI-1")


def _patch_common(monkeypatch, grow, *, threshold=2):
    monkeypatch.setattr(funnel, "normalize_gemini_mlb", lambda _rows: [grow])
    monkeypatch.setattr(funnel, "_kalshi_teams", lambda _event: ("AAA", "BBB"))
    monkeypatch.setattr(funnel, "_kalshi_identity", lambda _event, _market, _family: ("Jane Doe", threshold))
    monkeypatch.setattr(
        funnel,
        "kalshi_event_start_utc",
        lambda _event, _market: datetime.fromtimestamp(1000, tz=timezone.utc),
    )
    monkeypatch.setattr(funnel, "_discrete_threshold_and_side", lambda _comp, _line: (2, "YES"))
    monkeypatch.setattr(funnel, "SERIES_TO_FAMILY", {"KXMLBHITS": "baseball_player_hits"})
    monkeypatch.setattr(funnel, "FAMILY_TO_GEMINI_STAT", {"baseball_player_hits": "hits"})


def _event():
    return {
        "series_ticker": "KXMLBHITS",
        "markets": [{"ticker": "K-HITS-1"}],
    }


def test_exact_join_reaches_end_of_funnel(monkeypatch):
    grow = _grow(start_ms=1_000_000)
    _patch_common(monkeypatch, grow)
    out = funnel.build_join_funnel([_event()], [{}])
    assert out["order_entry"] is False
    assert out["certifies_execution"] is False
    assert out["stage_counts"]["exact_join_markets"] == 1
    assert out["families"]["baseball_player_hits"]["exact_join_markets"] == 1


def test_threshold_mismatch_is_explicit(monkeypatch):
    grow = _grow(start_ms=1_000_000)
    _patch_common(monkeypatch, grow, threshold=3)
    out = funnel.build_join_funnel([_event()], [{}])
    assert out["stage_counts"]["threshold_mismatch"] == 1
    assert out["stage_counts"].get("exact_join_markets", 0) == 0
    example = out["families"]["baseball_player_hits"]["examples"][0]
    assert example["reason"] == "threshold_mismatch"
    assert example["kalshi_threshold"] == "3"


def test_missing_gemini_stat_map_stops_before_market_join(monkeypatch):
    monkeypatch.setattr(funnel, "normalize_gemini_mlb", lambda _rows: [])
    monkeypatch.setattr(funnel, "SERIES_TO_FAMILY", {"KXMLBHRR": "baseball_player_hits_runs_rbis"})
    monkeypatch.setattr(funnel, "FAMILY_TO_GEMINI_STAT", {})
    event = {"series_ticker": "KXMLBHRR", "markets": [{"ticker": "K-HRR-1"}]}
    out = funnel.build_join_funnel([event], [])
    assert out["stage_counts"]["kalshi_events_missing_gemini_stat_map"] == 1
    assert out["stage_counts"].get("kalshi_markets_seen", 0) == 0
    assert out["missing_gemini_stat_families"] == ["baseball_player_hits_runs_rbis"]


def test_team_mismatch_is_distinguished_from_player_match(monkeypatch):
    grow = _grow(teams=("CCC", "DDD"), start_ms=1_000_000)
    _patch_common(monkeypatch, grow)
    out = funnel.build_join_funnel([_event()], [{}])
    assert out["stage_counts"]["has_stat_player_match"] == 1
    assert out["stage_counts"]["no_team_match"] == 1
    assert out["stage_counts"].get("has_team_match", 0) == 0

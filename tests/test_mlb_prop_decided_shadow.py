from decimal import Decimal

from src.mlb_prop_decided_shadow import (
    PropObservation,
    RetainedCandidates,
    StablePropTracker,
    _player_stat,
    prop_observation,
)
from src.strict_mlb_prop_snapshot import StrictCandidate


def _candidate() -> StrictCandidate:
    return StrictCandidate(
        family="baseball_player_hits",
        poly_slug="astatc-mlb-phi-nyy-2026-08-19-hits-bryceharper-gte2",
        kalshi_ticker="KXMLBHIT-26AUG19PHINYY-HARPER2",
        player="Bryce Harper",
        threshold=Decimal("2"),
        game_date="2026-08-19",
        start_skew_seconds=0.0,
    )


def _games(*, status="Live", hits=2, runs=1, rbi=3, total_bases=4):
    return {
        ("2026-08-19", ("nyy", "phi")): {
            "gamePk": 123,
            "status": status,
            "boxscore": {
                "teams": {
                    "away": {
                        "players": {
                            "ID1": {
                                "person": {"id": 1, "fullName": "Bryce Harper"},
                                "stats": {
                                    "batting": {
                                        "hits": hits,
                                        "runs": runs,
                                        "rbi": rbi,
                                        "totalBases": total_bases,
                                    }
                                },
                            }
                        }
                    },
                    "home": {"players": {}},
                }
            },
        }
    }


def test_player_stat_supported_families():
    row = next(iter(_games().values()))["boxscore"]["teams"]["away"]["players"]["ID1"]
    assert _player_stat(row, "baseball_player_hits") == 2
    assert _player_stat(row, "baseball_player_hits_runs_rbis") == 6
    assert _player_stat(row, "baseball_player_total_bases") == 4

    row["stats"]["pitching"] = {"strikeOuts": 7}
    assert _player_stat(row, "baseball_player_strikeouts") == 7


def test_live_threshold_reached_is_yes_research_state():
    c = _candidate()
    obs = prop_observation(
        family=c.family,
        player_name=c.player,
        poly_slug=c.poly_slug,
        threshold=c.threshold,
        games=_games(status="Live", hits=2),
    )
    assert obs is not None
    assert obs.outcome == "YES"
    assert obs.decision_state == "THRESHOLD_REACHED_STABLE_RESEARCH_ONLY"


def test_live_below_threshold_is_not_decided():
    c = _candidate()
    obs = prop_observation(
        family=c.family,
        player_name=c.player,
        poly_slug=c.poly_slug,
        threshold=c.threshold,
        games=_games(status="Live", hits=1),
    )
    assert obs is not None
    assert obs.outcome is None
    assert obs.decision_state is None


def test_final_below_threshold_is_no():
    c = _candidate()
    obs = prop_observation(
        family=c.family,
        player_name=c.player,
        poly_slug=c.poly_slug,
        threshold=c.threshold,
        games=_games(status="Final", hits=1),
    )
    assert obs is not None
    assert obs.outcome == "NO"
    assert obs.decision_state == "FINAL_STAT_STABLE_RESEARCH_ONLY"


def test_tracker_requires_time_and_repeated_identical_evidence():
    tracker = StablePropTracker(minimum_stable_ms=60_000, minimum_observations=3)
    obs = PropObservation(
        game_pk=1,
        game_status="Live",
        player="Bryce Harper",
        family="baseball_player_hits",
        statistic=2,
        threshold=Decimal("2"),
        outcome="YES",
        decision_state="THRESHOLD_REACHED_STABLE_RESEARCH_ONLY",
        source="MLB_STATS_API_GAME_1",
    )
    assert tracker.observe("k", obs, 0) is False
    assert tracker.observe("k", obs, 30_000) is False
    assert tracker.observe("k", obs, 60_000) is True


def test_tracker_resets_when_official_stat_changes():
    tracker = StablePropTracker(minimum_stable_ms=10, minimum_observations=2)
    first = PropObservation(1, "Live", "P", "baseball_player_hits", 2, Decimal("2"), "YES", "THRESHOLD_REACHED_STABLE_RESEARCH_ONLY", "MLB")
    changed = PropObservation(1, "Live", "P", "baseball_player_hits", 3, Decimal("2"), "YES", "THRESHOLD_REACHED_STABLE_RESEARCH_ONLY", "MLB")
    assert tracker.observe("k", first, 0) is False
    assert tracker.observe("k", changed, 20) is False
    assert tracker.status("k")["observations"] == 1


def test_retention_keeps_missing_candidate_until_ttl():
    retained = RetainedCandidates(ttl_seconds=10)
    c = _candidate()
    retained.refresh([c], 100.0)
    retained.refresh([], 109.9)
    assert retained.values() == [c]
    retained.refresh([], 110.1)
    assert retained.values() == []

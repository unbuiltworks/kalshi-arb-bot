from datetime import datetime, timedelta, timezone

from src import strict_mlb_prop_snapshot_v2 as v2


def test_next_day_evening_game_is_in_discovery_horizon() -> None:
    now = datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc)
    event = {"startTime": (now + timedelta(hours=22)).isoformat()}
    assert v2._nearby_event(event, now) is True


def test_game_beyond_discovery_horizon_is_excluded() -> None:
    now = datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc)
    event = {"startTime": (now + timedelta(hours=37)).isoformat()}
    assert v2._nearby_event(event, now) is False


def test_old_game_beyond_past_grace_is_excluded() -> None:
    now = datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc)
    event = {"startTime": (now - timedelta(hours=3)).isoformat()}
    assert v2._nearby_event(event, now) is False


def test_player_prop_rows_keep_only_supported_families() -> None:
    event = {
        "startTime": "2026-08-18T00:40:00Z",
        "markets": [
            {
                "slug": "astatc-mlb-lad-col-2026-08-17-hits-shooht-gte1",
                "sportsMarketType": "baseball_player_hits",
                "line": 1,
            },
            {
                "slug": "mlb-lad-col-2026-08-17-moneyline",
                "sportsMarketType": "baseball_team_full_game_winner",
            },
        ],
    }
    rows = v2._player_prop_rows(event)
    assert len(rows) == 1
    assert rows[0]["sportsMarketType"] == "baseball_player_hits"
    assert rows[0]["eventStartTime"] == "2026-08-18T00:40:00Z"

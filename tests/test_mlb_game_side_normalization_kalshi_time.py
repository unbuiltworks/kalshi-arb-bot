from datetime import datetime, timezone

from src.mlb_game_side_normalization import _kalshi_start


def test_kalshi_mlb_ticker_hhmm_is_eastern_wall_clock_in_summer() -> None:
    event = {"event_ticker": "KXMLBTOTAL-26AUG262105MINATH"}
    market = {"expected_expiration_time": "2026-08-27T04:05:00Z"}
    assert _kalshi_start(event, market) == datetime(2026, 8, 27, 1, 5, tzinfo=timezone.utc)


def test_kalshi_mlb_ticker_hhmm_observes_standard_time() -> None:
    event = {"event_ticker": "KXMLBGAME-26NOV261905NYYBOS"}
    market = {}
    assert _kalshi_start(event, market) == datetime(2026, 11, 27, 0, 5, tzinfo=timezone.utc)

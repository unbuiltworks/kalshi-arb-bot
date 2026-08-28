import pytest

from src.weather_paper_episode_tracker import advance_weather_snapshot


def _row():
    ticker = "KXHIGHMIA-26AUG21-B92.5"
    market_date = "2026-08-21"
    return {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "settlement_certified_shadow": True,
        "ev_certified_shadow": True,
        "exact_market_source_verified": True,
        "exact_market_source_audit": {
            "verified": True,
            "market_ticker": ticker,
            "market_date": market_date,
            "settlement_source_provider": "THE_WEATHER_COMPANY",
            "settlement_source_url": "https://weather.com/kalshi",
        },
        "blockers": [],
        "market_ticker": ticker,
        "market_date": market_date,
        "quote_observed_at": "2026-08-21T15:00:00+00:00",
        "fee_type": "quadratic",
        "fee_multiplier": "1",
        "best_shadow_side": {
            "side": "NO",
            "ask_cents": 36,
            "available_contracts": 10,
            "forecast_side_probability": "0.891",
        },
    }


def test_out_of_order_snapshot_fails_closed_without_shrinking_episode_time():
    _, state = advance_weather_snapshot(
        [_row()], prior_state={}, observed_ms=2000, snapshot_complete=True
    )
    original = {key: dict(value) for key, value in state.items()}

    with pytest.raises(ValueError, match="NON_MONOTONIC_WEATHER_OBSERVATION_TIME"):
        advance_weather_snapshot(
            [_row()], prior_state=state, observed_ms=1500, snapshot_complete=True
        )

    assert state == original
    assert next(iter(state.values()))["last_seen_ms"] == 2000


def test_non_integer_observation_timestamp_fails_closed():
    with pytest.raises(ValueError, match="OBSERVED_MS_MUST_BE_NONNEGATIVE_INTEGER"):
        advance_weather_snapshot(
            [_row()], prior_state={}, observed_ms="2000", snapshot_complete=True
        )

    with pytest.raises(ValueError, match="OBSERVED_MS_MUST_BE_NONNEGATIVE_INTEGER"):
        advance_weather_snapshot(
            [_row()], prior_state={}, observed_ms=True, snapshot_complete=True
        )


def test_corrupt_prior_episode_time_fails_closed():
    _, state = advance_weather_snapshot(
        [_row()], prior_state={}, observed_ms=2000, snapshot_complete=True
    )
    key = next(iter(state))
    state[key]["last_seen_ms"] = 1000
    state[key]["opened_ms"] = 2000

    with pytest.raises(ValueError, match="PRIOR_WEATHER_EPISODE_TIME_INVALID"):
        advance_weather_snapshot(
            [_row()], prior_state=state, observed_ms=3000, snapshot_complete=True
        )

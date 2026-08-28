from decimal import Decimal

from src.weather_episode_capacity_analyze import analyze_weather_episode_capacity


def _open(*, episode_id: str, observed_ms: int, cost_cents: str = "50", ev_cents: str = "5") -> dict:
    return {
        "event": "OPEN",
        "category": "weather",
        "classification": "PAPER",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "episode_id": episode_id,
        "trade_group_id": f"paper-{episode_id}",
        "identity": f"weather|KXHIGHABC-{episode_id}|2026-08-21|YES",
        "observed_ms": observed_ms,
        "quantity": 1,
        "ask_cents": 49,
        "modeled_fee_cents": "1",
        "paper_cost_cents": cost_cents,
        "modeled_after_fee_ev_cents": ev_cents,
        "modeled_after_fee_edge_cents_per_contract": ev_cents,
    }


def test_capacity_counts_only_unique_opens_not_moves_or_repeated_resting_depth():
    first = _open(episode_id="a", observed_ms=1_000)
    rows = [
        first,
        dict(first),
        {**first, "event": "MOVE", "observed_ms": 2_000},
        {**first, "event": "CLOSE", "observed_ms": 3_000},
        _open(episode_id="b", observed_ms=4_000, cost_cents="60", ev_cents="6"),
    ]
    result = analyze_weather_episode_capacity(
        rows,
        observation_start_ms=0,
        observation_end_ms=7_200_000,
        observation_window_verified=True,
    )

    assert result["unique_open_opportunities"] == 2
    assert Decimal(result["modeled_after_fee_ev_total_usd"]) == Decimal("0.11")
    assert Decimal(result["planned_capital_once_total_usd"]) == Decimal("1.10")
    assert Decimal(result["modeled_ev_usd_per_day"]) == Decimal("1.32")
    assert result["capital_hours"] is None
    assert result["capital_lock_status"] == "WITHHELD_UNTIL_OFFICIAL_SETTLEMENT_TIMESTAMPS"


def test_daily_projection_requires_literal_verified_full_window_and_minimum_runtime():
    row = _open(episode_id="a", observed_ms=10_000)

    string_true = analyze_weather_episode_capacity(
        [row], observation_start_ms=0, observation_end_ms=3_600_000, observation_window_verified="true"
    )
    assert string_true["modeled_ev_usd_per_day"] is None
    assert string_true["projection_withheld_reason"] == "OBSERVATION_WINDOW_UNVERIFIED"

    too_short = analyze_weather_episode_capacity(
        [row], observation_start_ms=0, observation_end_ms=359_999, observation_window_verified=True
    )
    assert too_short["modeled_ev_usd_per_day"] is None
    assert too_short["projection_withheld_reason"] == "OBSERVATION_WINDOW_TOO_SHORT"

    truncated = analyze_weather_episode_capacity(
        [row], observation_start_ms=20_000, observation_end_ms=3_620_000, observation_window_verified=True
    )
    assert truncated["modeled_ev_usd_per_day"] is None
    assert truncated["projection_withheld_reason"] == "OBSERVATION_WINDOW_DOES_NOT_COVER_ALL_OPENS"


def test_conflicting_duplicate_episode_fails_closed_instead_of_hindsight_selecting():
    a = _open(episode_id="same", observed_ms=1_000, ev_cents="5")
    b = {**a, "modeled_after_fee_ev_cents": "50"}
    result = analyze_weather_episode_capacity(
        [a, b], observation_start_ms=0, observation_end_ms=3_600_000, observation_window_verified=True
    )

    assert result["unique_open_opportunities"] == 0
    assert result["conflicting_episode_ids"] == ["same"]
    assert result["invalid_or_conflicting_open_rows"] == 1
    assert Decimal(result["modeled_after_fee_ev_total_usd"]) == Decimal("0")


def test_invalid_open_rows_are_excluded_and_liveish_rows_fail_closed():
    rows = [
        {**_open(episode_id="a", observed_ms=1_000), "read_only": "true"},
        {**_open(episode_id="b", observed_ms=2_000), "order_entry": True},
        {**_open(episode_id="c", observed_ms=3_000), "live_probe_ready": True},
        {**_open(episode_id="d", observed_ms=4_000), "paper_cost_cents": "nan"},
    ]
    result = analyze_weather_episode_capacity(rows)
    assert result["unique_open_opportunities"] == 0
    assert result["invalid_or_conflicting_open_rows"] == 4
    assert result["modeled_ev_usd_per_day"] is None


def test_bad_minimum_projection_window_fails_closed():
    try:
        analyze_weather_episode_capacity([], min_projection_window_ms=0)
    except ValueError as exc:
        assert str(exc) == "MIN_PROJECTION_WINDOW_MS_MUST_BE_POSITIVE_INTEGER"
    else:
        raise AssertionError("expected ValueError")

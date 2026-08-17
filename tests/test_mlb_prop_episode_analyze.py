from decimal import Decimal

from src.mlb_prop_episode_analyze import analyze_events


def _event(kind, observed_ms, *, opened_ms=0, profit="0.04", capital="1.00", last_seen_ms=None, probe_field="probe_1usd"):
    row = {
        "event": kind,
        "observed_ms": observed_ms,
        "opened_ms": opened_ms,
        "settlement_key": "hits|2026-08-17|Player|1|poly|kalshi",
        "probe_field": probe_field,
        "family": "hits",
        "direction": "POLY_YES_KALSHI_NO",
        "modeled_profit": profit,
        "capital": capital,
        "read_only": True,
        "order_entry": False,
    }
    if last_seen_ms is not None:
        row["last_seen_ms"] = last_seen_ms
    return row


def test_open_move_close_counts_one_episode_and_integrates_capital_hours():
    events = [
        _event("OPEN", 0, profit="0.04", capital="1.00"),
        _event("MOVE", 1000, profit="0.06", capital="0.80"),
        _event("CLOSE", 2500, profit="0.06", capital="0.80", last_seen_ms=2000),
    ]
    result = analyze_events(events)
    overall = result["overall"]
    assert overall["unique_episodes"] == 1
    assert overall["closed_episodes"] == 1
    assert Decimal(overall["first_seen_executable_shadow_profit"]) == Decimal("0.04")
    assert Decimal(overall["theoretical_peak_profit"]) == Decimal("0.06")
    expected = (Decimal("1.00") * Decimal(1000) + Decimal("0.80") * Decimal(1000)) / Decimal(3600000)
    assert Decimal(overall["capital_hours"]) == expected
    assert overall["max_episode_duration_ms"] == 2000


def test_repeated_resting_depth_cannot_be_double_counted_without_new_open():
    result = analyze_events([
        _event("OPEN", 0, profit="0.05"),
        _event("CLOSE", 5000, last_seen_ms=4000),
    ])
    assert result["overall"]["unique_episodes"] == 1
    assert Decimal(result["overall"]["first_seen_executable_shadow_profit"]) == Decimal("0.05")


def test_second_reopen_is_fresh_capacity_episode():
    first = [
        _event("OPEN", 0, opened_ms=0, profit="0.04"),
        _event("CLOSE", 1000, opened_ms=0, last_seen_ms=900),
    ]
    second = [
        _event("OPEN", 5000, opened_ms=5000, profit="0.03"),
        _event("CLOSE", 6000, opened_ms=5000, last_seen_ms=5900),
    ]
    result = analyze_events(first + second)
    assert result["overall"]["unique_episodes"] == 2
    assert Decimal(result["overall"]["first_seen_executable_shadow_profit"]) == Decimal("0.07")


def test_one_and_five_dollar_views_do_not_double_count_same_liquidity():
    events = [
        _event("OPEN", 0, profit="0.04", capital="0.90", probe_field="probe_1usd"),
        _event("CLOSE", 2000, profit="0.04", capital="0.90", last_seen_ms=1500, probe_field="probe_1usd"),
        _event("OPEN", 0, profit="0.16", capital="4.50", probe_field="probe_5usd"),
        _event("CLOSE", 2000, profit="0.16", capital="4.50", last_seen_ms=1500, probe_field="probe_5usd"),
    ]
    result = analyze_events(events)
    assert result["probe_view_episodes"] == 2
    assert result["unique_liquidity_episodes"] == 1
    assert result["by_probe"]["probe_1usd"]["unique_episodes"] == 1
    assert result["by_probe"]["probe_5usd"]["unique_episodes"] == 1
    assert result["overall"]["unique_episodes"] == 1
    assert Decimal(result["overall"]["first_seen_executable_shadow_profit"]) == Decimal("0.16")
    assert Decimal(result["by_family"]["hits"]["first_seen_executable_shadow_profit"]) == Decimal("0.16")


def test_cross_budget_dedup_prefers_higher_profit_not_larger_capital():
    events = [
        _event("OPEN", 0, profit="0.05", capital="0.95", probe_field="probe_1usd"),
        _event("OPEN", 0, profit="0.04", capital="4.80", probe_field="probe_5usd"),
    ]
    result = analyze_events(events)
    assert result["overall"]["unique_episodes"] == 1
    assert Decimal(result["overall"]["first_seen_executable_shadow_profit"]) == Decimal("0.05")


def test_daily_capacity_projection_is_withheld_without_full_observation_window():
    events = [
        _event("OPEN", 3_600_000, profit="0.10"),
        _event("CLOSE", 3_601_000, last_seen_ms=3_600_500),
    ]
    result = analyze_events(events)
    assert result["observation_window_provided"] is False
    assert result["observation_window_bounds_valid"] is False
    assert result["observation_window_covers_positive_episodes"] is False
    assert result["observation_window_invalid_reason"] == "missing_or_invalid_bounds"
    assert result["observation_span_hours"] is None
    assert result["capacity_projection_usd_per_day"] is None
    assert Decimal(result["positive_episode_span_hours"]) == Decimal("0.5") / Decimal(3600)


def test_daily_capacity_projection_uses_full_scanner_window_including_quiet_time():
    events = [
        _event("OPEN", 3_600_000, profit="0.10"),
        _event("CLOSE", 3_601_000, last_seen_ms=3_600_500),
    ]
    result = analyze_events(
        events,
        observation_start_ms=0,
        observation_end_ms=6 * 3_600_000,
    )
    assert result["observation_window_provided"] is True
    assert result["observation_window_bounds_valid"] is True
    assert result["observation_window_covers_positive_episodes"] is True
    assert result["observation_window_invalid_reason"] is None
    assert Decimal(result["observation_span_hours"]) == Decimal("6")
    assert Decimal(result["capacity_projection_usd_per_day"]) == Decimal("0.40")


def test_daily_capacity_projection_rejects_window_that_truncates_positive_episode():
    events = [
        _event("OPEN", 3_600_000, profit="0.10"),
        _event("CLOSE", 3_602_000, last_seen_ms=3_601_500),
    ]
    result = analyze_events(
        events,
        observation_start_ms=3_600_500,
        observation_end_ms=3_601_000,
    )
    assert result["observation_window_provided"] is False
    assert result["observation_window_bounds_valid"] is True
    assert result["observation_window_covers_positive_episodes"] is False
    assert result["observation_window_invalid_reason"] == "window_does_not_cover_all_positive_episodes"
    assert result["observation_span_hours"] is None
    assert result["capacity_projection_usd_per_day"] is None


def test_empty_positive_set_can_normalize_to_zero_over_explicit_valid_window():
    result = analyze_events([], observation_start_ms=0, observation_end_ms=3_600_000)
    assert result["observation_window_provided"] is True
    assert result["observation_window_covers_positive_episodes"] is True
    assert Decimal(result["capacity_projection_usd_per_day"]) == Decimal("0")


def test_invalid_or_non_read_only_events_fail_closed():
    bad = _event("OPEN", 0)
    bad["read_only"] = False
    result = analyze_events([bad])
    assert result["overall"]["unique_episodes"] == 0
    assert result["invalid_events"] == 1
    assert result["order_entry"] is False

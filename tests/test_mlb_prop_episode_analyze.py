from decimal import Decimal

from src.mlb_prop_episode_analyze import analyze_events


def _event(kind, observed_ms, *, opened_ms=0, profit="0.04", capital="1.00", last_seen_ms=None):
    row = {
        "event": kind,
        "observed_ms": observed_ms,
        "opened_ms": opened_ms,
        "settlement_key": "hits|2026-08-17|Player|1|poly|kalshi",
        "probe_field": "probe_1usd",
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


def test_invalid_or_non_read_only_events_fail_closed():
    bad = _event("OPEN", 0)
    bad["read_only"] = False
    result = analyze_events([bad])
    assert result["overall"]["unique_episodes"] == 0
    assert result["invalid_events"] == 1
    assert result["order_entry"] is False

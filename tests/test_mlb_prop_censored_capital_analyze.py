from decimal import Decimal

from src.mlb_prop_censored_capital_analyze import analyze_censored_capital


def _row(kind, observed_ms, *, capital="1.00", profit="0.04", last_seen_ms=None):
    row = {
        "event": kind,
        "observed_ms": observed_ms,
        "opened_ms": 0,
        "settlement_key": "hits|2026-08-18|Player|1|poly|kalshi",
        "family": "hits",
        "direction": "POLY_YES_KALSHI_NO",
        "capital": capital,
        "modeled_profit": profit,
        "read_only": True,
        "order_entry": False,
    }
    if last_seen_ms is not None:
        row["last_seen_ms"] = last_seen_ms
    return row


def test_interval_censored_close_reports_lower_and_upper_capital_hours():
    result = analyze_censored_capital([
        _row("OPEN", 0, capital="1.00", profit="0.04"),
        _row("MOVE", 1000, capital="0.80", profit="0.06"),
        _row("CLOSE", 2500, capital="0.80", profit="0.06", last_seen_ms=2000),
    ])

    assert result["episodes"] == 1
    assert result["closed_episodes"] == 1
    assert result["right_censored_episodes"] == 0
    assert result["interval_censored_closed_episodes"] == 1
    assert result["finite_capital_hours_upper_bound"] is True

    lower = (Decimal("1.00") * Decimal(1000) + Decimal("0.80") * Decimal(1000)) / Decimal(3600000)
    upper = (Decimal("1.00") * Decimal(1000) + Decimal("0.80") * Decimal(1500)) / Decimal(3600000)
    assert Decimal(result["capital_hours_lower_bound"]) == lower
    assert Decimal(result["capital_hours_upper_bound"]) == upper
    assert Decimal(result["capital_hours_known_through_log_end"]) == upper
    assert Decimal(result["conservative_profit_per_capital_hour_upper_bound"]) < Decimal(
        result["optimistic_profit_per_capital_hour_lower_bound"]
    )

    episode = result["episode_rows"][0]
    assert episode["duration_lower_bound_ms"] == 2000
    assert episode["duration_upper_bound_ms"] == 2500
    assert episode["last_observed_duration_ms"] == 2500
    assert episode["close_censoring_gap_ms"] == 500
    assert episode["finite_capital_hours_upper_bound"] is True
    assert episode["duration_censoring"] == "INTERVAL_CENSORED_POLLING"


def test_zero_censoring_gap_keeps_bounds_equal():
    result = analyze_censored_capital([
        _row("OPEN", 0),
        _row("CLOSE", 1000, last_seen_ms=1000),
    ])
    assert Decimal(result["capital_hours_lower_bound"]) == Decimal(result["capital_hours_upper_bound"])
    assert result["interval_censored_closed_episodes"] == 0


def test_right_censored_episode_withholds_finite_upper_bound_and_conservative_efficiency():
    result = analyze_censored_capital([
        _row("OPEN", 0, capital="1.00", profit="0.04"),
        _row("MOVE", 1000, capital="0.80", profit="0.06"),
    ])

    assert result["episodes"] == 1
    assert result["closed_episodes"] == 0
    assert result["right_censored_episodes"] == 1
    assert result["finite_capital_hours_upper_bound"] is False
    assert result["capital_hours_upper_bound"] is None
    assert result["conservative_profit_per_capital_hour_upper_bound"] is None
    assert Decimal(result["capital_hours_known_through_log_end"]) > 0

    episode = result["episode_rows"][0]
    assert episode["closed"] is False
    assert episode["duration_upper_bound_ms"] is None
    assert episode["close_censoring_gap_ms"] is None
    assert episode["capital_hours_upper_bound"] is None
    assert episode["conservative_profit_per_capital_hour_upper_bound"] is None
    assert episode["finite_capital_hours_upper_bound"] is False
    assert episode["duration_censoring"] == "RIGHT_CENSORED_ACTIVE_OR_LOG_ENDED"


def test_any_right_censored_episode_withholds_aggregate_upper_bound():
    closed = [
        _row("OPEN", 0),
        _row("CLOSE", 1000, last_seen_ms=900),
    ]
    active_open = _row("OPEN", 2000)
    active_open["opened_ms"] = 2000
    active_open["settlement_key"] = "hits|2026-08-18|Other Player|1|poly|kalshi"
    active_move = _row("MOVE", 3000)
    active_move["opened_ms"] = 2000
    active_move["settlement_key"] = "hits|2026-08-18|Other Player|1|poly|kalshi"

    result = analyze_censored_capital([*closed, active_open, active_move])

    assert result["episodes"] == 2
    assert result["closed_episodes"] == 1
    assert result["right_censored_episodes"] == 1
    assert result["capital_hours_upper_bound"] is None
    assert result["conservative_profit_per_capital_hour_upper_bound"] is None
    assert result["finite_capital_hours_upper_bound"] is False


def test_non_read_only_rows_fail_closed():
    bad = _row("OPEN", 0)
    bad["read_only"] = False
    result = analyze_censored_capital([bad])
    assert result["episodes"] == 0
    assert result["invalid_rows"] == 1
    assert result["order_entry"] is False
    assert result["live_probe_ready"] is False

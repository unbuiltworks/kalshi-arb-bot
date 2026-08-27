from datetime import datetime
from decimal import Decimal

from src.weather_portfolio_metric_binding_guard_v6 import (
    audit_weather_portfolio_metric_binding_v6,
)


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _row():
    row = {
        "market_ticker": "KXHIGHMIA-26AUG25-B92.5",
        "best_shadow_side": "NO",
        "side_economics": [
            {"side": "YES", "ask_cents": 60, "available_contracts": 1, "modeled_taker_fee_cents": 2, "modeled_after_fee_ev_cents": -22},
            {"side": "NO", "ask_cents": 41, "available_contracts": 1, "modeled_taker_fee_cents": "1.5", "modeled_after_fee_ev_cents": "17.5"},
        ],
        "net_edge_per_dollar": str(Decimal("7") / Decimal("17")),
        "depth_dollars": "0.41",
        "executable_probability": "0.25",
        "executable_probability_verified": True,
        "executable_probability_window_verified": True,
        "quote_observed_at": "2026-08-25T18:00:00-04:00",
        "executable_probability_window_start": "2026-08-25T16:00:00-04:00",
        "executable_probability_window_end": "2026-08-25T18:00:00-04:00",
        "executable_probability_time_scope_verified": True,
        "executable_probability_recency_verified": True,
        "executable_probability_max_age_seconds": "60",
        "executable_probability_window_span_verified": True,
        "executable_probability_max_window_seconds": "7200",
        "executable_probability_success_count": 2,
        "executable_probability_sample_count": 8,
        "executable_probability_sample_basis": "COMPLETE_SCANNER_SNAPSHOT_WINDOW",
        "executable_probability_complete_window_verified": True,
        "executable_probability_includes_zero_opportunity_snapshots": True,
        "executable_probability_duplicate_snapshot_counting_excluded": True,
        "executable_probability_scope_verified": True,
        "executable_probability_sample_scope": "EXACT_MARKET_SAME_SIDE_EXECUTABLE_SHADOW",
        "executable_probability_market_ticker": "KXHIGHMIA-26AUG25-B92.5",
        "executable_probability_side": "NO",
        "executable_probability_quantity": "1",
        "executable_probability_max_ask_dollars": "0.41",
        "executable_probability_outcome_definition_verified": True,
        "executable_probability_outcome_definition": "EXACT_MARKET_SAME_SIDE_QTY_PRICE_MIN_AFTER_FEE_EDGE",
        "executable_probability_min_after_fee_edge_cents_per_contract": "17.5",
        "executable_probability_episode_basis_verified": True,
        "executable_probability_episode_basis": "UNIQUE_OPEN_EPISODES_COMPLETE_WINDOW",
        "executable_probability_resting_liquidity_deduplicated": True,
        "executable_probability_open_move_close_lifecycle_verified": True,
        "executable_probability_success_episode_count": 2,
        "executable_probability_unique_episode_count": 8,
        "executable_probability_episode_identity_verified": True,
        "executable_probability_episode_identity_scope": "EXACT_MARKET_SAME_SIDE_QTY_PRICE_MIN_AFTER_FEE_EDGE",
        "executable_probability_episode_ids": [f"ep-{i}" for i in range(8)],
        "executable_probability_success_episode_ids": ["ep-1", "ep-6"],
    }
    base_ms = _ms("2026-08-25T16:00:00-04:00")
    row["executable_probability_episode_records"] = [
        {
            "episode_id": f"ep-{i}",
            "event": "OPEN",
            "category": "weather",
            "market_ticker": row["market_ticker"],
            "side": "NO",
            "quantity": 1,
            "ask_cents": 41,
            "available_contracts": 1 if i in {1, 6} else "0.5",
            "modeled_after_fee_edge_cents_per_contract": "17.5",
            "execution_success": i in {1, 6},
            "opened_ms": base_ms + i * 60_000,
            "observed_ms": base_ms + i * 60_000,
            "read_only": True,
            "order_entry": False,
            "live_probe_ready": False,
        }
        for i in range(8)
    ]
    return row


def test_depth_proven_open_outcomes_verify():
    out = audit_weather_portfolio_metric_binding_v6(_row())
    assert out["weather_portfolio_metric_binding_verified"] is True
    assert out["weather_execution_episode_depth_verified"] is True
    assert out["weather_execution_episode_success_depth_consistent"] is True


def test_success_without_scored_quantity_depth_fails_closed():
    row = _row()
    row["executable_probability_episode_records"][1]["available_contracts"] = "0.99"
    out = audit_weather_portfolio_metric_binding_v6(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_EPISODE_SUCCESS_DEPTH_MISMATCH" in out["blockers"]


def test_failure_with_enough_depth_fails_closed():
    row = _row()
    row["executable_probability_episode_records"][0]["available_contracts"] = 1
    out = audit_weather_portfolio_metric_binding_v6(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_EPISODE_SUCCESS_DEPTH_MISMATCH" in out["blockers"]


def test_missing_depth_evidence_fails_closed():
    row = _row()
    row["executable_probability_episode_records"][0].pop("available_contracts")
    out = audit_weather_portfolio_metric_binding_v6(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_EPISODE_DEPTH_UNVERIFIED" in out["blockers"]


def test_string_success_flag_fails_closed():
    row = _row()
    row["executable_probability_episode_records"][1]["execution_success"] = "true"
    out = audit_weather_portfolio_metric_binding_v6(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_EPISODE_SUCCESS_DEPTH_MISMATCH" in out["blockers"]

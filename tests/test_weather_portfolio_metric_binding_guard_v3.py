from decimal import Decimal

from src.weather_portfolio_metric_binding_guard_v3 import (
    audit_weather_portfolio_metric_binding_v3,
)


def _row():
    return {
        "market_ticker": "KXHIGHMIA-26AUG25-B92.5",
        "best_shadow_side": "NO",
        "side_economics": [
            {
                "side": "YES",
                "ask_cents": 60,
                "available_contracts": 1,
                "modeled_taker_fee_cents": 2,
                "modeled_after_fee_ev_cents": -22,
            },
            {
                "side": "NO",
                "ask_cents": 41,
                "available_contracts": 1,
                "modeled_taker_fee_cents": "1.5",
                "modeled_after_fee_ev_cents": "17.5",
            },
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
        "executable_probability_success_count": 10,
        "executable_probability_sample_count": 40,
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


def test_explicit_episode_sets_can_verify_metric_binding():
    out = audit_weather_portfolio_metric_binding_v3(_row())
    assert out["weather_portfolio_metric_binding_verified"] is True
    assert out["weather_execution_episode_identity_verified"] is True
    assert out["weather_execution_episode_set_reconciled"] is True
    assert out["verified_executable_probability_episode_ids"] == [f"ep-{i}" for i in range(8)]
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_duplicate_episode_ids_fail_closed():
    row = _row()
    row["executable_probability_episode_ids"][-1] = "ep-0"
    out = audit_weather_portfolio_metric_binding_v3(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_EPISODE_IDENTITY_UNVERIFIED" in out["blockers"]


def test_success_episode_ids_must_be_subset_of_population():
    row = _row()
    row["executable_probability_success_episode_ids"] = ["ep-1", "ep-outside"]
    out = audit_weather_portfolio_metric_binding_v3(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert out["weather_execution_episode_set_reconciled"] is False
    assert "WEATHER_EXECUTION_EPISODE_SET_RECONCILIATION_UNVERIFIED" in out["blockers"]


def test_episode_counts_must_match_explicit_sets():
    row = _row()
    row["executable_probability_unique_episode_count"] = 7
    out = audit_weather_portfolio_metric_binding_v3(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_EPISODE_SET_RECONCILIATION_UNVERIFIED" in out["blockers"]


def test_episode_identity_scope_must_be_literal_and_exact():
    for value in ("true", None, "EXACT_MARKET"):
        row = _row()
        row["executable_probability_episode_identity_scope"] = value
        out = audit_weather_portfolio_metric_binding_v3(row)
        assert out["weather_portfolio_metric_binding_verified"] is False
        assert "WEATHER_EXECUTION_EPISODE_IDENTITY_UNVERIFIED" in out["blockers"]

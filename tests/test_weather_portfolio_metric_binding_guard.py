from decimal import Decimal

from src.weather_portfolio_metric_binding_guard import audit_weather_portfolio_metric_binding


def _row():
    # NO: 41c q1 + 1.5c fee = 42.5c deployed capital; 17.5c EV.
    # Derived edge/dollar = 0.175 / 0.425 = 7/17.
    # Empirical execution probability = 10 / 40 = 0.25.
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
    }


def test_portfolio_metrics_bind_to_selected_side():
    out = audit_weather_portfolio_metric_binding(_row())
    assert out["weather_portfolio_metric_binding_verified"] is True
    assert out["weather_execution_probability_verified"] is True
    assert out["weather_execution_sample_basis_verified"] is True
    assert out["weather_execution_sample_scope_verified"] is True
    assert out["weather_execution_sample_outcome_verified"] is True
    assert out["weather_execution_sample_time_verified"] is True
    assert Decimal(out["derived_depth_dollars"]) == Decimal("0.41")
    assert Decimal(out["derived_net_edge_per_dollar"]) == Decimal("7") / Decimal("17")
    assert Decimal(out["verified_executable_probability"]) == Decimal("0.25")
    assert Decimal(out["selected_after_fee_edge_cents_per_contract"]) == Decimal("17.5")
    assert out["executable_probability_success_count"] == 10
    assert out["executable_probability_sample_count"] == 40
    assert out["blockers"] == []
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_inflated_edge_cannot_pass():
    row = _row()
    row["net_edge_per_dollar"] = "9"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_PORTFOLIO_METRICS_UNVERIFIED" in out["blockers"]


def test_inflated_depth_cannot_pass():
    row = _row()
    row["depth_dollars"] = "500"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_PORTFOLIO_METRICS_UNVERIFIED" in out["blockers"]


def test_execution_probability_needs_empirical_verification():
    for key, value in (
        ("executable_probability_verified", "true"),
        ("executable_probability_window_verified", False),
        ("executable_probability_sample_count", 0),
        ("executable_probability_success_count", -1),
        ("executable_probability", "1.01"),
    ):
        row = _row()
        row[key] = value
        out = audit_weather_portfolio_metric_binding(row)
        assert out["weather_portfolio_metric_binding_verified"] is False
        assert "WEATHER_EXECUTION_PROBABILITY_UNVERIFIED" in out["blockers"]


def test_execution_probability_requires_complete_nonduplicated_snapshot_basis():
    for key, value in (
        ("executable_probability_sample_basis", "POSITIVE_ONLY_SNAPSHOTS"),
        ("executable_probability_complete_window_verified", "true"),
        ("executable_probability_includes_zero_opportunity_snapshots", False),
        ("executable_probability_duplicate_snapshot_counting_excluded", None),
    ):
        row = _row()
        row[key] = value
        out = audit_weather_portfolio_metric_binding(row)
        assert out["weather_execution_sample_basis_verified"] is False
        assert out["weather_execution_probability_verified"] is False
        assert out["weather_portfolio_metric_binding_verified"] is False
        assert "WEATHER_EXECUTION_SAMPLE_BASIS_UNVERIFIED" in out["blockers"]
        assert "WEATHER_EXECUTION_PROBABILITY_UNVERIFIED" in out["blockers"]


def test_execution_probability_requires_exact_market_side_price_and_quantity_scope():
    cases = (
        ("executable_probability_scope_verified", "true"),
        ("executable_probability_sample_scope", "ALL_WEATHER_MARKETS"),
        ("executable_probability_market_ticker", "KXHIGHNY-26AUG25-B82.5"),
        ("executable_probability_side", "YES"),
        ("executable_probability_quantity", "5"),
        ("executable_probability_max_ask_dollars", "0.45"),
    )
    for key, value in cases:
        row = _row()
        row[key] = value
        out = audit_weather_portfolio_metric_binding(row)
        assert out["weather_execution_sample_scope_verified"] is False
        assert out["weather_execution_probability_verified"] is False
        assert out["weather_portfolio_metric_binding_verified"] is False
        assert "WEATHER_EXECUTION_SAMPLE_SCOPE_UNVERIFIED" in out["blockers"]
        assert "WEATHER_EXECUTION_PROBABILITY_UNVERIFIED" in out["blockers"]


def test_execution_probability_requires_same_after_fee_edge_threshold():
    row = _row()
    row["executable_probability_min_after_fee_edge_cents_per_contract"] = "1"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_execution_sample_outcome_verified"] is False
    assert out["weather_execution_probability_verified"] is False
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_SAMPLE_OUTCOME_UNVERIFIED" in out["blockers"]
    assert "WEATHER_EXECUTION_PROBABILITY_UNVERIFIED" in out["blockers"]


def test_execution_probability_outcome_definition_must_be_explicit_and_literal():
    for key, value in (
        ("executable_probability_outcome_definition_verified", "true"),
        ("executable_probability_outcome_definition", "EXECUTABLE_AT_PRICE_ONLY"),
        ("executable_probability_min_after_fee_edge_cents_per_contract", None),
    ):
        row = _row()
        row[key] = value
        out = audit_weather_portfolio_metric_binding(row)
        assert out["weather_execution_sample_outcome_verified"] is False
        assert out["weather_portfolio_metric_binding_verified"] is False
        assert "WEATHER_EXECUTION_SAMPLE_OUTCOME_UNVERIFIED" in out["blockers"]


def test_missing_market_identity_cannot_borrow_weather_wide_execution_rate():
    row = _row()
    del row["market_ticker"]
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_execution_sample_scope_verified"] is False
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_SAMPLE_SCOPE_UNVERIFIED" in out["blockers"]


def test_producer_probability_must_equal_probability_derived_from_counts():
    row = _row()
    row["executable_probability"] = "0.90"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert out["weather_execution_probability_verified"] is False
    assert "WEATHER_EXECUTION_PROBABILITY_UNVERIFIED" in out["blockers"]


def test_success_count_cannot_exceed_total_count():
    row = _row()
    row["executable_probability_success_count"] = 41
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert out["verified_executable_probability"] is None
    assert "WEATHER_EXECUTION_PROBABILITY_UNVERIFIED" in out["blockers"]


def test_fixed_point_fractional_selected_side_is_supported():
    row = _row()
    row["side_economics"][1] = {
        "side": "NO",
        "ask_dollars": "0.4100",
        "quantity": "2.50",
        "modeled_taker_fee_dollars": "0.025",
        "modeled_after_fee_ev_cents": "45",
    }
    row["depth_dollars"] = "1.025"
    row["net_edge_per_dollar"] = str(Decimal("0.45") / Decimal("1.05"))
    row["executable_probability_quantity"] = "2.50"
    row["executable_probability_max_ask_dollars"] = "0.4100"
    row["executable_probability_min_after_fee_edge_cents_per_contract"] = "18"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_portfolio_metric_binding_verified"] is True
    assert Decimal(out["derived_depth_dollars"]) == Decimal("1.025")


def test_q1_execution_rate_cannot_certify_q5_capacity():
    row = _row()
    row["side_economics"][1] = {
        "side": "NO",
        "ask_dollars": "0.41",
        "quantity": "5",
        "modeled_taker_fee_dollars": "0.075",
        "modeled_after_fee_ev_cents": "87.5",
    }
    row["depth_dollars"] = "2.05"
    row["net_edge_per_dollar"] = str(Decimal("0.875") / Decimal("2.125"))
    row["executable_probability_min_after_fee_edge_cents_per_contract"] = "17.5"
    row["executable_probability_quantity"] = "1"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_execution_sample_scope_verified"] is False
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_SAMPLE_SCOPE_UNVERIFIED" in out["blockers"]


def test_worse_price_execution_rate_cannot_certify_cheaper_edge():
    row = _row()
    row["executable_probability_max_ask_dollars"] = "0.45"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_execution_sample_scope_verified"] is False
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_SAMPLE_SCOPE_UNVERIFIED" in out["blockers"]


def test_missing_selected_side_economics_fails_closed():
    row = _row()
    row["side_economics"] = []
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_PORTFOLIO_METRICS_UNVERIFIED" in out["blockers"]


def test_execution_probability_rejects_later_liquidity_lookahead():
    row = _row()
    row["executable_probability_window_end"] = "2026-08-25T18:00:01-04:00"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_execution_sample_time_verified"] is False
    assert out["execution_probability_lookahead_detected"] is True
    assert out["weather_execution_probability_verified"] is False
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_SAMPLE_LOOKAHEAD_DETECTED" in out["blockers"]
    assert "WEATHER_EXECUTION_PROBABILITY_UNVERIFIED" in out["blockers"]


def test_execution_probability_rejects_stale_historical_liquidity_cohort():
    row = _row()
    row["executable_probability_window_end"] = "2026-08-25T17:58:59-04:00"
    row["executable_probability_max_age_seconds"] = "60"
    out = audit_weather_portfolio_metric_binding(row)
    assert out["weather_execution_sample_time_verified"] is False
    assert out["weather_execution_probability_verified"] is False
    assert out["weather_portfolio_metric_binding_verified"] is False
    assert "WEATHER_EXECUTION_SAMPLE_STALE" in out["blockers"]
    assert "WEATHER_EXECUTION_PROBABILITY_UNVERIFIED" in out["blockers"]


def test_execution_probability_recency_needs_explicit_audited_bound():
    for key, value in (
        ("executable_probability_recency_verified", "true"),
        ("executable_probability_max_age_seconds", None),
        ("executable_probability_max_age_seconds", "0"),
    ):
        row = _row()
        row[key] = value
        out = audit_weather_portfolio_metric_binding(row)
        assert out["weather_execution_sample_time_verified"] is False
        assert out["weather_execution_probability_verified"] is False
        assert out["weather_portfolio_metric_binding_verified"] is False
        assert "WEATHER_EXECUTION_SAMPLE_RECENCY_UNVERIFIED" in out["blockers"]


def test_execution_probability_time_scope_must_be_literal_and_timezone_aware():
    for key, value in (
        ("executable_probability_time_scope_verified", "true"),
        ("quote_observed_at", "2026-08-25T18:00:00"),
        ("executable_probability_window_start", "2026-08-25T16:00:00"),
        ("executable_probability_window_end", None),
    ):
        row = _row()
        row[key] = value
        out = audit_weather_portfolio_metric_binding(row)
        assert out["weather_execution_sample_time_verified"] is False
        assert out["weather_execution_probability_verified"] is False
        assert out["weather_portfolio_metric_binding_verified"] is False
        assert "WEATHER_EXECUTION_SAMPLE_TIME_UNVERIFIED" in out["blockers"]

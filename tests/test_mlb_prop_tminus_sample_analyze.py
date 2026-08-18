from src.mlb_prop_tminus_sample_analyze import analyze_sample_witnessed_tminus


def _row(*, observed_ms: int, profit: str, budget: str = "5") -> dict:
    return {
        "family": "baseball_player_hits",
        "poly_slug": "astatc-mlb-aaa-bbb-2026-08-18-hits-player-gte1",
        "kalshi_ticker": "KXMLBHIT-26AUG18AAABBB-PLAYER-1",
        "rule_status": "RULES_PENDING",
        "synchronized_book_pair": True,
        "poly_received_ms": observed_ms - 10,
        "kalshi_received_ms": observed_ms,
        "read_only": True,
        "order_entry": False,
        "execution_eligible": False,
        f"probe_{budget}usd": {
            "direction": "POLY_YES_KALSHI_NO",
            "quantity": 1,
            "total_cost_upper_bound": str(1 - float(profit)),
            "conservative_locked_profit": profit,
        },
    }


def _snapshot(row1=None, row5=None) -> dict:
    return {
        "read_only": True,
        "order_entry": False,
        "execution_eligible_pairs": 0,
        "top_probe_1usd": [row1] if row1 else [],
        "top_probe_5usd": [row5] if row5 else [],
    }


def test_best_profit_tminus_uses_actual_sample_timestamp_not_episode_open():
    first = _row(observed_ms=1_000_000, profit="0.03")
    best = _row(observed_ms=1_000_400, profit="0.09")
    later = _row(observed_ms=1_000_800, profit="0.05")
    start = 1_000_000 + 10 * 60_000
    result = analyze_sample_witnessed_tminus(
        [_snapshot(row5=first), _snapshot(row5=best), _snapshot(row5=later)],
        start_ms_by_poly_slug={first["poly_slug"]: start},
    )
    ep = result["episodes"][0]
    assert result["unique_positive_episodes"] == 1
    assert result["theoretical_unique_episode_profit_usd"] == "0.09"
    assert ep["opened_ms"] == 1_000_000
    assert ep["best_profit_observed_ms"] == 1_000_400
    assert ep["t_minus_ms_at_open"] == 10 * 60_000
    assert ep["t_minus_ms_at_best_observed_profit"] == 10 * 60_000 - 400
    assert ep["best_profit_timing_precision"] == "SYNCHRONIZED_REST_SAMPLE_WITNESS"
    assert ep["continuous_subsecond_survival_proven"] is False


def test_cross_budget_views_are_not_additive():
    row1 = _row(observed_ms=2_000_000, profit="0.03", budget="1")
    row5 = _row(observed_ms=2_000_000, profit="0.11", budget="5")
    result = analyze_sample_witnessed_tminus(
        [_snapshot(row1=row1, row5=row5)],
        start_ms_by_poly_slug={row1["poly_slug"]: 2_600_000},
    )
    assert result["unique_positive_episodes"] == 1
    assert result["theoretical_unique_episode_profit_usd"] == "0.11"
    assert result["episodes"][0]["best_observed_profit_usd"] == "0.11"


def test_sparse_samples_do_not_claim_continuous_survival():
    first = _row(observed_ms=3_000_000, profit="0.04")
    second = _row(observed_ms=3_001_000, profit="0.08")
    result = analyze_sample_witnessed_tminus(
        [_snapshot(row5=first), _snapshot(row5=second)],
        start_ms_by_poly_slug={first["poly_slug"]: 3_600_000},
    )
    ep = result["episodes"][0]
    assert ep["best_profit_observed_ms"] == 3_001_000
    assert ep["continuous_subsecond_survival_proven"] is False
    assert result["live_probe_ready"] is False


def test_missing_start_keeps_best_sample_but_timing_bucket_unknown():
    row = _row(observed_ms=4_000_000, profit="0.06")
    result = analyze_sample_witnessed_tminus(
        [_snapshot(row5=row)],
        start_ms_by_poly_slug={row["poly_slug"]: None},
    )
    ep = result["episodes"][0]
    assert ep["best_profit_observed_ms"] == 4_000_000
    assert ep["t_minus_ms_at_best_observed_profit"] is None
    assert ep["t_minus_bucket_at_best_observed_profit"] == "UNKNOWN_START"

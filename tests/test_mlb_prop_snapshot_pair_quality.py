from src.mlb_prop_snapshot_pair_quality import analyze_snapshots


def _row(*, observed_ms: int, profit: str = "0.05", cost: str = "0.95", budget: str = "5"):
    return {
        "family": "baseball_player_hits",
        "poly_slug": "astatc-mlb-aaa-bbb-2026-08-18-hits-player-gte1",
        "kalshi_ticker": "KXMLBHIT-26AUG18AAABBB-PLAYER-1",
        "rule_status": "PARTICIPATION_GATED",
        "synchronized_book_pair": True,
        "poly_received_ms": observed_ms - 10,
        "kalshi_received_ms": observed_ms,
        "read_only": True,
        "order_entry": False,
        "execution_eligible": False,
        f"probe_{budget}usd": {
            "direction": "POLY_YES_KALSHI_NO",
            "quantity": 1,
            "total_cost_upper_bound": cost,
            "conservative_locked_profit": profit,
        },
    }


def _snapshot(row1=None, row5=None):
    return {
        "read_only": True,
        "order_entry": False,
        "execution_eligible_pairs": 0,
        "top_probe_1usd": [row1] if row1 else [],
        "top_probe_5usd": [row5] if row5 else [],
    }


def test_same_pair_in_both_budget_views_is_not_double_counted():
    row1 = _row(observed_ms=1_000, profit="0.03", cost="0.97", budget="1")
    row5 = _row(observed_ms=1_000, profit="0.11", cost="4.89", budget="5")
    result = analyze_snapshots([_snapshot(row1, row5)])
    assert result["unique_exact_pairs"] == 1
    assert result["unique_positive_episodes"] == 1
    assert result["theoretical_unique_episode_profit_usd"] == "0.11"
    assert result["capital_if_each_unique_episode_taken_once_usd"] == "4.89"


def test_sparse_rest_samples_do_not_claim_subsecond_survival():
    first = _row(observed_ms=1_000)
    second = _row(observed_ms=2_000)
    result = analyze_snapshots([_snapshot(row5=first), _snapshot(row5=second)])
    episode = result["episodes"][0]
    assert episode["survival_sampling"]["500"] == "UNRESOLVED_SPARSE_REST_SAMPLING"
    assert episode["continuous_subsecond_survival_proven"] is False
    assert result["continuous_subsecond_survival_proven"] is False


def test_dense_repeated_positive_samples_are_labeled_sampled_not_continuous():
    snapshots = [
        _snapshot(row5=_row(observed_ms=1_000)),
        _snapshot(row5=_row(observed_ms=1_100)),
        _snapshot(row5=_row(observed_ms=1_200)),
        _snapshot(row5=_row(observed_ms=1_300)),
        _snapshot(row5=_row(observed_ms=1_400)),
        _snapshot(row5=_row(observed_ms=1_500)),
    ]
    result = analyze_snapshots(snapshots, informative_sample_gap_ms=150)
    episode = result["episodes"][0]
    assert episode["survival_sampling"]["50"] == "SAMPLED_POSITIVE_THROUGH_THRESHOLD"
    assert episode["survival_sampling"]["100"] == "SAMPLED_POSITIVE_THROUGH_THRESHOLD"
    assert episode["survival_sampling"]["250"] == "SAMPLED_POSITIVE_THROUGH_THRESHOLD"
    assert episode["survival_sampling"]["500"] == "SAMPLED_POSITIVE_THROUGH_THRESHOLD"
    assert episode["continuous_subsecond_survival_proven"] is False


def test_unsafe_snapshot_or_unsynchronized_row_is_ignored():
    unsafe = _snapshot(row5=_row(observed_ms=1_000))
    unsafe["order_entry"] = True
    unsync_row = _row(observed_ms=2_000)
    unsync_row["synchronized_book_pair"] = False
    result = analyze_snapshots([unsafe, _snapshot(row5=unsync_row)])
    assert result["unique_exact_pairs"] == 0
    assert result["unique_positive_episodes"] == 0
    assert result["live_probe_ready"] is False


def test_stringly_readiness_flags_fail_closed():
    row = _row(observed_ms=1_000)
    row["full_rules_audited"] = "false"
    row["live_probe_ready"] = "true"
    result = analyze_snapshots([_snapshot(row5=row)])
    episode = result["episodes"][0]
    assert episode["full_rules_audited"] is False
    assert episode["live_probe_ready"] is False

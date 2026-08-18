from src.mlb_prop_tminus_analyze import analyze_tminus


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


def test_cross_budget_same_episode_is_counted_once_in_tminus_bucket():
    row1 = _row(observed_ms=1_000_000, profit="0.03", budget="1")
    row5 = _row(observed_ms=1_000_000, profit="0.11", budget="5")
    start = 1_000_000 + 10 * 60_000
    result = analyze_tminus(
        [_snapshot(row1=row1, row5=row5)],
        start_ms_by_poly_slug={row1["poly_slug"]: start},
    )
    assert result["unique_positive_episodes"] == 1
    assert result["episode_open_tminus_buckets"] == {"T_5_15M": 1}
    assert result["theoretical_unique_episode_profit_usd"] == "0.11"
    assert result["episodes"][0]["t_minus_ms_at_open"] == 10 * 60_000
    assert result["live_probe_ready"] is False


def test_unknown_start_fails_closed_without_dropping_episode():
    row = _row(observed_ms=2_000_000, profit="0.05")
    result = analyze_tminus(
        [_snapshot(row5=row)],
        start_ms_by_poly_slug={row["poly_slug"]: None},
    )
    assert result["unique_positive_episodes"] == 1
    assert result["episodes_with_unknown_start"] == 1
    assert result["episode_open_tminus_buckets"] == {"UNKNOWN_START": 1}
    assert result["episodes"][0]["t_minus_ms_at_open"] is None


def test_post_start_episode_is_separate_bucket():
    row = _row(observed_ms=3_000_000, profit="0.05")
    result = analyze_tminus(
        [_snapshot(row5=row)],
        start_ms_by_poly_slug={row["poly_slug"]: 2_999_999},
    )
    assert result["episode_open_tminus_buckets"] == {"POST_START": 1}


def test_quiet_snapshot_does_not_manufacture_timing_capacity():
    result = analyze_tminus(
        [_snapshot()],
        start_ms_by_poly_slug={},
    )
    assert result["unique_positive_episodes"] == 0
    assert result["episode_open_tminus_buckets"] == {}
    assert result["theoretical_unique_episode_profit_usd"] == "0"

from decimal import Decimal

from src.shadow_episode_economics import build_episodes, summarize_episodes


def sample(ts: int, *, pair: str = "p1", profit: str | None = "0.05", direction: str = "A") -> dict:
    row = {
        "ts_ms": ts,
        "pair_key": pair,
        "family": "baseball_player_hits",
    }
    if profit is not None:
        row["positive_conservative_plan"] = {
            "direction": direction,
            "quantity": 2,
            "total_cost_upper_bound": "1.90",
            "conservative_locked_profit": profit,
        }
    return row


def test_repeated_snapshots_collapse_to_one_episode() -> None:
    episodes = build_episodes([
        sample(1000),
        sample(1010, profit="0.04"),
        sample(1020, profit="0.03"),
        sample(1030, profit=None),
    ])
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.samples == 3
    assert ep.lifetime_ms == 30
    assert ep.opening_profit == Decimal("0.05")
    assert ep.minimum_profit == Decimal("0.03")


def test_gap_starts_new_episode_instead_of_double_counting_snapshots() -> None:
    episodes = build_episodes([
        sample(0),
        sample(100),
        sample(2500),
    ], max_gap_ms=1000)
    assert len(episodes) == 2
    assert episodes[0].lifetime_ms == 1100
    assert episodes[1].opened_ms == 2500


def test_direction_change_closes_old_episode() -> None:
    episodes = build_episodes([
        sample(1000, direction="A"),
        sample(1020, direction="B"),
        sample(1040, profit=None),
    ])
    assert len(episodes) == 2
    assert episodes[0].direction == "A"
    assert episodes[0].lifetime_ms == 20
    assert episodes[1].direction == "B"
    assert episodes[1].lifetime_ms == 20


def test_summary_counts_one_fill_per_episode_and_survival() -> None:
    episodes = build_episodes([
        sample(0, profit="0.05"),
        sample(10, profit="0.04"),
        sample(25, profit=None),
        sample(100, pair="p2", profit="0.02"),
        sample(103, pair="p2", profit=None),
    ])
    result = summarize_episodes(
        episodes,
        observation_start_ms=0,
        observation_end_ms=1000,
    )
    assert result["unique_episodes"] == 2
    assert result["one_fill_per_episode_shadow_profit"] == "0.07"
    assert result["minimum_observed_profit_sum"] == "0.06"
    assert result["survival_ms"]["20"] == 1
    assert result["survival_ms"]["5"] == 1
    assert result["run_rate_status"] == "SHORT_SHADOW_SAMPLE_NOT_A_FORECAST"
    assert result["read_only"] is True
    assert result["order_entry"] is False


def test_non_positive_samples_do_not_create_episode() -> None:
    rows = [sample(1000, profit=None)]
    assert build_episodes(rows) == []


def test_explicit_settlement_key_is_used_when_present() -> None:
    row = sample(1000)
    row["settlement_key"] = "mlb:exact:key"
    episodes = build_episodes([row])
    assert episodes[0].pair_key == "mlb:exact:key"

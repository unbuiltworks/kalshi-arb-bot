from decimal import Decimal

from src.gemini_kalshi_episode_report import build_episodes, summarize


def _row(ts, *, profit="1.00", qty="10", net_cost="0.95", fresh=True, direction="KALSHI_YES_GEMINI_NO"):
    return {
        "ts_ms": ts,
        "asset": "BTC",
        "window_end_utc": "2026-08-17T04:00:00+00:00",
        "quote_skew_ms": 80,
        "fresh": fresh,
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "both_taker_fee_candidates": [] if Decimal(profit) <= 0 else [{
            "direction": direction,
            "quantity": qty,
            "net_cost": net_cost,
            "locked_profit": profit,
        }],
    }


def test_repeated_resting_depth_counts_once_per_episode():
    rows = [
        _row(1_000, profit="0.40", qty="10", net_cost="0.96"),
        _row(2_000, profit="0.60", qty="10", net_cost="0.94"),
        _row(3_000, profit="0.50", qty="10", net_cost="0.95"),
        _row(4_000, profit="0"),
    ]
    episodes = build_episodes(rows)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.samples == 3
    assert ep.duration_ms == 2_000
    assert ep.best_locked_profit == Decimal("0.60")
    assert ep.best_quantity == Decimal("10")
    assert ep.best_net_cost == Decimal("0.94")

    report = summarize(rows)
    btc = report["assets"]["BTC"]
    assert btc["unique_fee_positive_episodes"] == 1
    assert btc["episode_best_shadow_profit_dollars"] == "0.60"
    assert report["accounting"] == "ONE_BEST_OBSERVATION_PER_EPISODE_NO_RESTING_DEPTH_SUM"
    assert report["promotion_status"] == "RESEARCH_ONLY_BLOCKED"


def test_nonpositive_sample_splits_episode():
    rows = [
        _row(1_000, profit="0.20"),
        _row(2_000, profit="0"),
        _row(3_000, profit="0.30"),
    ]
    episodes = build_episodes(rows)
    assert len(episodes) == 2
    assert [ep.best_locked_profit for ep in episodes] == [Decimal("0.20"), Decimal("0.30")]


def test_stale_rows_never_create_fee_positive_episode():
    rows = [_row(1_000, profit="5.00", fresh=False)]
    report = summarize(rows)
    assert report["total_unique_fee_positive_episodes"] == 0
    assert report["assets"] == {}


def test_gap_larger_than_tolerance_splits_episode_even_without_negative_sample():
    rows = [
        _row(1_000, profit="0.20"),
        _row(5_000, profit="0.30"),
    ]
    episodes = build_episodes(rows, gap_tolerance_ms=2_500)
    assert len(episodes) == 2

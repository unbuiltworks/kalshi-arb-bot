from decimal import Decimal

from src.gemini_kalshi_watch_analyze import analyze_rows


def _row(ts_ms: int, *, g_ask="0.45", g_bid="0.44", k_bid="0.51", k_ask="0.52", size="10"):
    return {
        "ts_ms": ts_ms,
        "asset": "BTC",
        "window_end_utc": "2026-08-15T05:00:00+00:00",
        "gemini_symbol": "GEMI-BTC15M2608150500-UP",
        "kalshi_ticker": "KXBTC15M-TEST",
        "gemini_yes_bid": g_bid,
        "gemini_yes_bid_size": size,
        "gemini_yes_ask": g_ask,
        "gemini_yes_ask_size": size,
        "kalshi_yes_bid": k_bid,
        "kalshi_yes_bid_size": size,
        "kalshi_yes_ask": k_ask,
        "kalshi_yes_ask_size": size,
        "fresh": True,
    }


def test_repeated_positive_samples_are_one_episode_not_repeated_pnl():
    rows = [
        _row(1_000),
        _row(2_000),
        _row(3_000, g_ask="0.55", k_bid="0.40"),
    ]
    result = analyze_rows(rows, fee_multiplier=Decimal("1"))
    btc = result["by_asset"]["BTC"]
    assert btc["both_fee_positive_samples"] == 2
    assert btc["unique_positive_episodes"] == 1
    assert result["unique_positive_episodes"] == 1
    assert Decimal(btc["shadow_profit_dollars_one_execution_per_episode"]) > 0
    assert btc["max_observed_positive_span_ms"] == 1000
    assert btc["sample_interval_median_ms"] == 1000
    assert btc["survival_episode_counts"]["500"] is None
    assert result["survival_status"] == "UNRESOLVED_BELOW_SAMPLE_CADENCE"
    assert result["unresolved_survival_thresholds_ms"] == [50, 100, 250, 500]
    assert result["repeated_depth_counted_as_pnl"] is False


def test_subsecond_sampling_can_credit_observed_survival_thresholds():
    rows = [
        _row(1_000),
        _row(1_050),
        _row(1_100),
        _row(1_150),
        _row(1_200),
        _row(1_250),
        _row(1_300),
        _row(1_350),
        _row(1_400),
        _row(1_450),
        _row(1_500),
        _row(1_550, g_ask="0.55", k_bid="0.40"),
    ]
    result = analyze_rows(rows, fee_multiplier=Decimal("1"))
    btc = result["by_asset"]["BTC"]
    assert btc["sample_interval_median_ms"] == 50
    assert btc["survival_episode_counts"] == {"50": 1, "100": 1, "250": 1, "500": 1}
    assert result["survival_status"] == "OBSERVED_AT_SAMPLE_CADENCE"
    assert result["unresolved_survival_thresholds_ms"] == []


def test_one_cent_raw_lock_is_killed_by_both_taker_fees():
    # Gemini YES 0.47 + Kalshi NO (1 - 0.48) 0.52 = 0.99 raw cost.
    # At one contract, cent-rounded taker fees erase the apparent one-cent lock.
    rows = [_row(1_000, g_ask="0.47", g_bid="0.46", k_bid="0.48", k_ask="0.49", size="1")]
    result = analyze_rows(rows, fee_multiplier=Decimal("1"))
    btc = result["by_asset"]["BTC"]
    assert btc["both_fee_positive_samples"] == 0
    assert btc["unique_positive_episodes"] == 0
    assert Decimal(result["shadow_profit_dollars_one_execution_per_episode"]) == 0

from decimal import Decimal

from src.gemini_kalshi_authoritative_fee_analyze import analyze_with_authoritative_metadata
from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata


def _meta(asset: str, *, fee_type: str = "quadratic", multiplier: str = "1"):
    return KalshiSeriesFeeMetadata(
        series_ticker=f"KX{asset}15M",
        fee_type=fee_type,
        fee_multiplier=multiplier,
        last_updated_ts="2026-08-16T00:00:00Z",
        scheduled_changes=(),
    )


def _eth_row(ts_ms: int = 1_786_924_384_253):
    # Runtime-shaped example: Kalshi YES .65 + Gemini NO .28 = .93 raw cost.
    # For 416 contracts, Gemini fee is $5.88 and Kalshi fee is $6.63,
    # leaving $16.61 after both modeled taker fees before settlement-basis risk.
    return {
        "ts_ms": ts_ms,
        "asset": "ETH",
        "window_end_utc": "2026-08-17T00:00:00+00:00",
        "gemini_symbol": "GEMI-ETH15M2608170000-UP",
        "kalshi_ticker": "KXETH15M-26AUG162000-00",
        "fresh": True,
        "gemini_yes_bid": "0.7200",
        "gemini_yes_bid_size": "416.00",
        "gemini_yes_ask": "0.8000",
        "gemini_yes_ask_size": "1500.00",
        "kalshi_yes_bid": "0.6100",
        "kalshi_yes_bid_size": "542.00",
        "kalshi_yes_ask": "0.6500",
        "kalshi_yes_ask_size": "417.98",
    }


def test_authoritative_replay_prices_observed_eth_example_after_both_fees():
    result = analyze_with_authoritative_metadata([_eth_row()], {"ETH": _meta("ETH")})
    eth = result["by_asset"]["ETH"]
    assert eth["fee_status"] == "MODELED_FROM_SERIES_METADATA_RESEARCH_ONLY"
    assert eth["unique_positive_episodes"] == 1
    assert Decimal(eth["best_episode_profit_dollars"]) == Decimal("16.610000")
    assert Decimal(result["shadow_profit_dollars_one_execution_per_episode"]) == Decimal("16.610000")
    assert Decimal(result["basis_unadjusted_shadow_profit_dollars_one_execution_per_episode"]) == Decimal("16.610000")
    assert result["settlement_status"] == "INDEX_MISMATCH_RESEARCH_ONLY"
    assert result["basis_adjusted"] is False
    assert result["settlement_equivalent"] is False
    assert result["order_entry"] is False


def test_authoritative_replay_withholds_daily_projection_under_index_mismatch():
    rows = [_eth_row(1_786_924_384_253), _eth_row(1_786_924_444_253)]
    result = analyze_with_authoritative_metadata(rows, {"ETH": _meta("ETH")})
    assert result["observation_ms"] == 60_000
    assert result["projected_shadow_profit_per_day"] is None
    assert result["naive_projected_shadow_profit_per_day"] is None
    assert result["projection_status"] == "WITHHELD_SETTLEMENT_INDEX_MISMATCH_RESEARCH_ONLY"
    assert result["projection_warning"] == "BASIS_UNADJUSTED_SHADOW_PNL_NOT_A_FORECAST"


def test_authoritative_replay_fails_closed_for_unmapped_fee_type():
    result = analyze_with_authoritative_metadata(
        [_eth_row()],
        {"ETH": _meta("ETH", fee_type="flat")},
    )
    eth = result["by_asset"]["ETH"]
    assert eth["fee_status"] == "UNMAPPED_DO_NOT_TRADE"
    assert result["unique_positive_episodes"] == 0
    assert Decimal(result["shadow_profit_dollars_one_execution_per_episode"]) == 0


def test_authoritative_replay_fails_closed_when_metadata_missing():
    result = analyze_with_authoritative_metadata([_eth_row()], {})
    assert result["by_asset"]["ETH"]["fee_status"] == "FEE_METADATA_UNAVAILABLE_DO_NOT_TRADE"
    assert result["unique_positive_episodes"] == 0

from datetime import datetime, timezone
from decimal import Decimal

from src.gemini_kalshi_lock_settlement_join import fee_positive_windows, summarize_join
from src.gemini_kalshi_settlement_agreement import AgreementRow


def _row(ts_ms: int, *, ticker: str = "KXBTC15M-TEST"):
    return {
        "asset": "BTC",
        "window_end_utc": "2026-08-15T05:00:00+00:00",
        "gemini_symbol": "GEMI-BTC15M-TEST-UP",
        "kalshi_ticker": ticker,
        "ts_ms": ts_ms,
        "fresh": True,
        "gemini_yes_bid": "0.60",
        "gemini_yes_bid_size": "10",
        "gemini_yes_ask": "0.61",
        "gemini_yes_ask_size": "10",
        "kalshi_yes_bid": "0.35",
        "kalshi_yes_bid_size": "10",
        "kalshi_yes_ask": "0.36",
        "kalshi_yes_ask_size": "10",
    }


def test_fee_positive_windows_dedupes_repeated_samples():
    locks = fee_positive_windows(
        [_row(1_000), _row(2_000)],
        fee_multiplier=Decimal("1"),
    )
    assert len(locks) == 1
    payload = next(iter(locks.values()))
    assert payload["asset"] == "BTC"
    assert payload["kalshi_ticker"] == "KXBTC15M-TEST"
    assert Decimal(payload["best_locked_profit_dollars"]) > 0


def test_summarize_join_compares_lock_disagreement_to_baseline():
    locks = fee_positive_windows([_row(1_000)], fee_multiplier=Decimal("1"))
    expiry = datetime(2026, 8, 15, 5, 0, tzinfo=timezone.utc)
    agreements = [
        AgreementRow(
            asset="BTC",
            expiry=expiry,
            gemini_ticker="GEMI-BTC15M-TEST",
            kalshi_ticker="KXBTC15M-TEST",
            gemini_outcome="yes",
            kalshi_outcome="no",
            agrees=False,
            gemini_strike=100.0,
            gemini_settlement=100.01,
            gemini_margin_bps=1.0,
        ),
        AgreementRow(
            asset="ETH",
            expiry=expiry,
            gemini_ticker="GEMI-ETH15M-OTHER",
            kalshi_ticker="KXETH15M-OTHER",
            gemini_outcome="yes",
            kalshi_outcome="yes",
            agrees=True,
            gemini_strike=100.0,
            gemini_settlement=101.0,
            gemini_margin_bps=100.0,
        ),
    ]
    result = summarize_join(locks, agreements)
    assert result["fee_positive_lock_windows"] == 1
    assert result["settled_lock_windows_joined"] == 1
    assert result["lock_window_disagreements"] == 1
    assert result["lock_window_disagreement_rate"] == 1.0
    assert result["baseline_disagreement_rate"] == 0.5
    assert result["by_asset"]["BTC"]["disagreement_rate"] == 1.0
    assert result["classification"] == "INDEX_MISMATCH_RESEARCH_ONLY"
    assert result["order_entry"] is False

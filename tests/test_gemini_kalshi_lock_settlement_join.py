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
    assert Decimal(payload["best_net_cost_dollars"]) > 0


def test_summarize_join_marks_both_win_mismatch_to_actual_payout():
    locks = fee_positive_windows([_row(1_000)], fee_multiplier=Decimal("1"))
    payload = next(iter(locks.values()))
    payload["direction"] = "GEMINI_YES_KALSHI_NO"
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
    row = result["disagreement_rows"][0]
    qty = Decimal(row["best_quantity"])
    net_cost = Decimal(row["best_net_cost_dollars"])
    assert row["direction"] == "GEMINI_YES_KALSHI_NO"
    assert Decimal(row["settlement_payout_dollars"]) == qty * 2
    assert Decimal(row["settlement_adjusted_pnl_dollars"]) == qty * 2 - net_cost
    assert result["fee_positive_lock_windows"] == 1
    assert result["settled_lock_windows_joined"] == 1
    assert result["lock_window_disagreements"] == 1
    assert result["lock_window_disagreement_rate"] == 1.0
    assert result["baseline_disagreement_rate"] == 0.5
    assert result["by_asset"]["BTC"]["disagreement_rate"] == 1.0
    assert result["favorable_mismatch_windows"] == 1
    assert result["adverse_mismatch_windows"] == 0
    assert result["basis_margin_sensitivity"]["1"]["eligible_windows"] == 1
    assert result["basis_margin_sensitivity"]["2"]["eligible_windows"] == 0
    assert result["classification"] == "INDEX_MISMATCH_RESEARCH_ONLY"
    assert result["order_entry"] is False


def test_summarize_join_marks_both_lose_mismatch_to_negative_cost():
    locks = fee_positive_windows([_row(1_000)], fee_multiplier=Decimal("1"))
    payload = next(iter(locks.values()))
    payload["direction"] = "KALSHI_YES_GEMINI_NO"
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
        )
    ]
    result = summarize_join(locks, agreements)
    row = result["disagreement_rows"][0]
    assert Decimal(row["settlement_payout_dollars"]) == 0
    assert Decimal(row["settlement_adjusted_pnl_dollars"]) == -Decimal(row["best_net_cost_dollars"])
    assert result["adverse_mismatch_windows"] == 1
    assert result["favorable_mismatch_windows"] == 0
    assert result["basis_margin_sensitivity"]["0.5"]["disagreements"] == 1
    assert result["basis_margin_sensitivity"]["2"]["disagreements"] == 0

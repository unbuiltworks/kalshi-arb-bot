from datetime import datetime, timezone
from decimal import Decimal

from src.gemini_kalshi_aligned_watch import Pair
from src.gemini_kalshi_btc_probe_candidate import qualified_report
from src.gemini_kalshi_execution_shadow import (
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

D = Decimal


def test_qualified_report_recomputes_exact_one_contract_edge():
    pair = Pair(
        asset="BTC",
        window_end=datetime(2026, 8, 15, 22, 30, tzinfo=timezone.utc),
        gemini_symbol="GEMI-BTC15M2608152230-UP",
        kalshi_ticker="KXBTC15M-TEST",
    )
    gemini = TopOfBook(
        venue="gemini",
        market_id=pair.gemini_symbol,
        ts_ms=1000,
        yes_bid=D("0.39"),
        yes_bid_size=D("10"),
        yes_ask=D("0.40"),
        yes_ask_size=D("10"),
    )
    kalshi = TopOfBook(
        venue="kalshi",
        market_id=pair.kalshi_ticker,
        ts_ms=1010,
        yes_bid=D("0.50"),
        yes_bid_size=D("10"),
        yes_ask=D("0.51"),
        yes_ask_size=D("10"),
    )

    def kalshi_fee(price, quantity):
        return kalshi_quadratic_taker_fee(price, quantity, fee_multiplier=D("1"))

    candidates = evaluate_pair(
        gemini,
        kalshi,
        gemini_fee=gemini_predictions_taker_fee,
        kalshi_fee=kalshi_fee,
        max_quote_skew_ms=50,
        max_quantity=D("1"),
    )
    candidate = next(c for c in candidates if c.direction == "GEMINI_YES_KALSHI_NO")
    report = qualified_report(
        pair=pair,
        gemini=gemini,
        kalshi=kalshi,
        candidate=candidate,
        kalshi_fee_multiplier=D("1"),
        persistence_ms=10,
    )

    assert report["qualified"] is True
    assert report["quantity"] == "1"
    assert D(report["recomputed_locked_profit_dollars"]) == D("0.06")
    assert report["recomputed_locked_profit_dollars"] == report["modeled_locked_profit_dollars"]
    assert report["gemini_payload"]["timeInForce"] == "FOK"
    assert report["kalshi_payload"]["time_in_force"] == "fill_or_kill"
    assert report["read_only"] is True
    assert report["order_entry"] is False

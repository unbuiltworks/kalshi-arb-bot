from decimal import Decimal

from src.gemini_kalshi_both_fee_shadow import evaluate_both_fee_shadow
from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata

D = Decimal


def _book(venue: str, *, bid: str, ask: str, size: str = "100", ts_ms: int = 1000):
    return TopOfBook(
        venue=venue,
        market_id=venue,
        ts_ms=ts_ms,
        yes_bid=D(bid),
        yes_bid_size=D(size),
        yes_ask=D(ask),
        yes_ask_size=D(size),
    )


def _meta(fee_type: str = "quadratic", multiplier: str = "1"):
    return KalshiSeriesFeeMetadata(
        series_ticker="KXBTC15M",
        fee_type=fee_type,
        fee_multiplier=multiplier,
        last_updated_ts="2026-08-16T00:00:00Z",
        scheduled_changes=(),
    )


def test_both_fee_shadow_uses_series_multiplier_and_keeps_settlement_blocker():
    # Kalshi YES 0.081 + Gemini NO 0.880 = 0.961 raw cost, mirroring the
    # observed BTC shape. Both-fee result should remain positive at useful size.
    gemini = _book("gemini", bid="0.120", ask="0.130", size="94.01", ts_ms=1000)
    kalshi = _book("kalshi", bid="0.080", ask="0.081", size="94.01", ts_ms=1036)
    result = evaluate_both_fee_shadow(
        gemini,
        kalshi,
        kalshi_fee_metadata=_meta(),
        max_quote_skew_ms=500,
    )
    assert result.positive
    assert result.kalshi_fee_status == "MODELED_FROM_SERIES_METADATA_RESEARCH_ONLY"
    assert result.settlement_status == "INDEX_MISMATCH_RESEARCH_ONLY"
    assert result.order_entry is False
    best = max(result.candidates, key=lambda c: c.locked_profit)
    assert best.direction == "KALSHI_YES_GEMINI_NO"
    assert best.locked_profit > 0


def test_unknown_kalshi_fee_type_fails_closed_without_candidate():
    result = evaluate_both_fee_shadow(
        _book("gemini", bid="0.120", ask="0.130"),
        _book("kalshi", bid="0.080", ask="0.081"),
        kalshi_fee_metadata=_meta(fee_type="mystery"),
    )
    assert result.candidates == ()
    assert result.kalshi_fee_status == "UNMAPPED_DO_NOT_TRADE"
    assert result.settlement_status == "INDEX_MISMATCH_RESEARCH_ONLY"


def test_stale_cross_venue_quotes_do_not_create_both_fee_candidate():
    result = evaluate_both_fee_shadow(
        _book("gemini", bid="0.120", ask="0.130", ts_ms=1000),
        _book("kalshi", bid="0.080", ask="0.081", ts_ms=2000),
        kalshi_fee_metadata=_meta(),
        max_quote_skew_ms=500,
    )
    assert result.candidates == ()

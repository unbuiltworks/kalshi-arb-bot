from decimal import Decimal

from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata
from src.gemini_kalshi_runtime_fee import evaluate_with_authoritative_kalshi_fee

D = Decimal


def book(venue: str, market_id: str, *, bid: str, ask: str, bid_size: str = "10", ask_size: str = "10", ts_ms: int = 1000) -> TopOfBook:
    return TopOfBook(
        venue=venue,
        market_id=market_id,
        ts_ms=ts_ms,
        yes_bid=D(bid),
        yes_bid_size=D(bid_size),
        yes_ask=D(ask),
        yes_ask_size=D(ask_size),
    )


def meta(asset: str, fee_type: str = "quadratic", multiplier: str = "1") -> KalshiSeriesFeeMetadata:
    series = {
        "BTC": "KXBTC15M",
        "ETH": "KXETH15M",
        "SOL": "KXSOL15M",
        "XRP": "KXXRP15M",
    }[asset]
    return KalshiSeriesFeeMetadata(
        series_ticker=series,
        fee_type=fee_type,
        fee_multiplier=multiplier,
        last_updated_ts="2026-08-16T00:00:00Z",
        scheduled_changes=(),
    )


def test_eth_observed_shape_is_positive_after_both_modeled_fees():
    # Observed research shape: Gemini NO ask=0.28 (YES bid=0.72), Kalshi YES ask=0.65.
    g = book("gemini", "ETH", bid="0.72", ask="0.73", bid_size="416", ask_size="416")
    k = book("kalshi", "KXETH15M-X", bid="0.64", ask="0.65", bid_size="416", ask_size="416", ts_ms=1063)
    result = evaluate_with_authoritative_kalshi_fee(
        "ETH", g, k, {"ETH": meta("ETH")}, max_quote_skew_ms=500
    )
    assert result.modeled is True
    assert result.fee_status == "MODELED_FROM_SERIES_METADATA_RESEARCH_ONLY"
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.direction == "KALSHI_YES_GEMINI_NO"
    assert candidate.quantity == D("416")
    assert candidate.raw_cost == D("386.88")
    assert candidate.fees == D("12.51")
    assert candidate.locked_profit == D("16.61")


def test_missing_metadata_fails_closed():
    g = book("gemini", "BTC", bid="0.50", ask="0.51")
    k = book("kalshi", "KXBTC15M-X", bid="0.40", ask="0.41")
    result = evaluate_with_authoritative_kalshi_fee("BTC", g, k, {})
    assert result.candidates == ()
    assert result.fee_status == "FEE_METADATA_UNAVAILABLE_DO_NOT_TRADE"


def test_unmapped_fee_type_fails_closed():
    g = book("gemini", "SOL", bid="0.70", ask="0.71")
    k = book("kalshi", "KXSOL15M-X", bid="0.60", ask="0.61")
    result = evaluate_with_authoritative_kalshi_fee(
        "SOL", g, k, {"SOL": meta("SOL", fee_type="flat")}
    )
    assert result.candidates == ()
    assert result.fee_status == "UNMAPPED_DO_NOT_TRADE"


def test_wrong_series_metadata_fails_closed():
    g = book("gemini", "XRP", bid="0.70", ask="0.71")
    k = book("kalshi", "KXXRP15M-X", bid="0.60", ask="0.61")
    wrong = meta("BTC")
    result = evaluate_with_authoritative_kalshi_fee("XRP", g, k, {"XRP": wrong})
    assert result.candidates == ()
    assert result.fee_status == "FEE_SERIES_MISMATCH_DO_NOT_TRADE"


def test_quote_skew_gate_still_applies():
    g = book("gemini", "BTC", bid="0.72", ask="0.73", ts_ms=1000)
    k = book("kalshi", "KXBTC15M-X", bid="0.64", ask="0.65", ts_ms=1600)
    result = evaluate_with_authoritative_kalshi_fee(
        "BTC", g, k, {"BTC": meta("BTC")}, max_quote_skew_ms=500
    )
    assert result.modeled is True
    assert result.candidates == ()

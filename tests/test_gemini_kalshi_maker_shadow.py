from decimal import Decimal

from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata
from src.gemini_kalshi_maker_shadow import (
    KalshiTrade,
    RestingQuote,
    apply_trade_to_quote,
    desired_maker_price,
    kalshi_maker_fee,
    maker_side_book,
    max_safe_maker_price,
    parse_trade,
    quote_economics,
)

D = Decimal


def _meta(fee_type="quadratic", multiplier="1"):
    return KalshiSeriesFeeMetadata(
        series_ticker="KXXRP15M",
        fee_type=fee_type,
        fee_multiplier=multiplier,
        last_updated_ts="2026-08-04T00:00:00Z",
        scheduled_changes=(),
    )


def _gemini(*, bid="0.08", bid_size="10", ask="0.09", ask_size="10", ts=1000):
    return TopOfBook(
        venue="gemini",
        market_id="G",
        ts_ms=ts,
        yes_bid=D(bid),
        yes_bid_size=D(bid_size),
        yes_ask=D(ask),
        yes_ask_size=D(ask_size),
    )


def _kalshi(*, bid="0.14", bid_size="7", ask="0.16", ask_size="5", ts=1000):
    return TopOfBook(
        venue="kalshi",
        market_id="K",
        ts_ms=ts,
        yes_bid=D(bid),
        yes_bid_size=D(bid_size),
        yes_ask=D(ask),
        yes_ask_size=D(ask_size),
    )


def _quote(outcome="NO", price="0.84", ahead="2"):
    direction = (
        "GEMINI_YES_KALSHI_NO"
        if outcome == "NO"
        else "KALSHI_YES_GEMINI_NO"
    )
    hedge = "YES" if outcome == "NO" else "NO"
    return RestingQuote(
        key="q",
        asset="XRP",
        direction=direction,
        maker_outcome=outcome,
        hedge_outcome=hedge,
        policy="JOIN",
        target_profit=D("0.03"),
        price=D(price),
        opened_ms=1000,
        queue_ahead=D(ahead),
    )


def test_current_quadratic_series_has_zero_maker_fee():
    assert kalshi_maker_fee(
        D("0.84"), D("1"), fee_type="quadratic", fee_multiplier=D("1")
    ) == D("0")


def test_maker_fee_series_uses_centicent_trade_component():
    assert kalshi_maker_fee(
        D("0.50"),
        D("1"),
        fee_type="quadratic_with_maker_fees",
        fee_multiplier=D("1"),
    ) == D("0.0044")


def test_no_maker_quote_plus_gemini_hedge_computes_profit():
    econ = quote_economics(
        direction="GEMINI_YES_KALSHI_NO",
        maker_price=D("0.84"),
        gemini=_gemini(ask="0.09"),
        fee_meta=_meta(),
    )
    assert econ is not None
    assert econ.all_in_cost == D("0.94")
    assert econ.locked_profit == D("0.06")
    assert econ.maker_fee == D("0")


def test_max_safe_price_respects_required_profit():
    price = max_safe_maker_price(
        direction="GEMINI_YES_KALSHI_NO",
        gemini=_gemini(ask="0.09"),
        fee_meta=_meta(),
        target_profit=D("0.03"),
    )
    assert price == D("0.87")
    econ = quote_economics(
        direction="GEMINI_YES_KALSHI_NO",
        maker_price=price,
        gemini=_gemini(ask="0.09"),
        fee_meta=_meta(),
    )
    assert econ is not None
    assert econ.locked_profit == D("0.03")


def test_post_only_policies_never_cross_ask():
    assert desired_maker_price(
        policy="JOIN",
        best_bid=D("0.82"),
        best_ask=D("0.86"),
        economic_max=D("0.87"),
    ) == D("0.82")
    assert desired_maker_price(
        policy="IMPROVE_1T",
        best_bid=D("0.82"),
        best_ask=D("0.86"),
        economic_max=D("0.87"),
    ) == D("0.83")
    assert desired_maker_price(
        policy="MAX_SAFE",
        best_bid=D("0.82"),
        best_ask=D("0.86"),
        economic_max=D("0.87"),
    ) == D("0.85")


def test_no_side_book_uses_transformed_yes_ask_for_bid_and_queue():
    best_bid, size, best_ask = maker_side_book(_kalshi(), "NO")
    assert best_bid == D("0.84")
    assert size == D("5")
    assert best_ask == D("0.86")


def test_public_trade_parser_uses_fixed_point_fields():
    trade = parse_trade(
        {
            "type": "trade",
            "msg": {
                "market_ticker": "K",
                "yes_price_dollars": "0.1600",
                "no_price_dollars": "0.8400",
                "count_fp": "3.00",
                "taker_side": "yes",
                "ts_ms": 1234,
            },
        },
        "K",
    )
    assert trade is not None
    assert trade.no_price == D("0.8400")
    assert trade.quantity == D("3.00")
    assert trade.taker_side == "yes"


def test_join_quote_consumes_queue_ahead_before_fill():
    quote = _quote(outcome="NO", price="0.84", ahead="2")
    first = KalshiTrade(
        ticker="K",
        yes_price=D("0.16"),
        no_price=D("0.84"),
        quantity=D("1"),
        taker_side="yes",
        ts_ms=1100,
    )
    assert apply_trade_to_quote(quote, first) == D("0")
    assert quote.queue_ahead == D("1")

    second = KalshiTrade(
        ticker="K",
        yes_price=D("0.16"),
        no_price=D("0.84"),
        quantity=D("1.5"),
        taker_side="yes",
        ts_ms=1200,
    )
    assert apply_trade_to_quote(quote, second) == D("0.5")
    assert quote.queue_ahead == D("0")
    assert quote.remaining == D("0.5")


def test_trade_through_is_high_confidence_fill():
    quote = _quote(outcome="YES", price="0.15", ahead="10")
    trade = KalshiTrade(
        ticker="K",
        yes_price=D("0.14"),
        no_price=D("0.86"),
        quantity=D("0.25"),
        taker_side="no",
        ts_ms=1200,
    )
    assert apply_trade_to_quote(quote, trade) == D("1")
    assert quote.remaining == D("0")


def test_wrong_taker_side_does_not_fill_quote():
    quote = _quote(outcome="NO", price="0.84", ahead="0")
    trade = KalshiTrade(
        ticker="K",
        yes_price=D("0.16"),
        no_price=D("0.84"),
        quantity=D("20"),
        taker_side="no",
        ts_ms=1200,
    )
    assert apply_trade_to_quote(quote, trade) == D("0")

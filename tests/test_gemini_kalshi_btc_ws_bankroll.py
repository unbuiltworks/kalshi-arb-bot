from decimal import Decimal

from src.gemini_kalshi_btc_ws_bankroll import simulate


def _row(ts: int, *, quantity: str = "100", positive: bool = True):
    candidates = []
    if positive:
        candidates = [{
            "direction": "KALSHI_YES_GEMINI_NO",
            "quantity": quantity,
            "net_cost": str(Decimal("0.95") * Decimal(quantity)),
            "fees": "0.10",
            "locked_profit": str(Decimal("0.05") * Decimal(quantity)),
            "roi": str(Decimal("0.05") / Decimal("0.95")),
            "quote_skew_ms": 5,
        }]
    return {
        "asset": "BTC",
        "window_end_utc": "2026-08-15T12:15:00+00:00",
        "gemini_symbol": "GEMI-BTC15M-W1-UP",
        "kalshi_ticker": "KXBTC15M-W1",
        "receive_ts_ms": ts,
        "gemini_receive_ts_ms": ts - 3,
        "kalshi_receive_ts_ms": ts - 5,
        "kalshi_exchange_to_receive_ms": 7,
        "gemini_yes_bid": "0.60",
        "gemini_yes_bid_size": quantity,
        "gemini_yes_ask": "0.61",
        "gemini_yes_ask_size": quantity,
        "kalshi_yes_bid": "0.34",
        "kalshi_yes_bid_size": quantity,
        "kalshi_yes_ask": "0.35",
        "kalshi_yes_ask_size": quantity,
        "both_taker_fee_candidates": candidates,
        "read_only": True,
        "order_entry": False,
    }


def test_trade_cap_reduces_position_size_and_tracks_peak_venue_capital():
    rows = [_row(1000), _row(1015), _row(1100, positive=False)]
    result = simulate(
        rows,
        threshold_ms=10,
        kalshi_bankroll=Decimal("4000"),
        trade_cap=Decimal("100"),
    )
    assert result["trades_taken"] == 1
    assert Decimal(result["total_capital_deployed_dollars"]) <= Decimal("100")
    assert Decimal(result["peak_kalshi_capital_locked_dollars"]) < Decimal("4000")
    assert Decimal(result["shadow_profit_dollars"]) > 0
    assert result["order_entry"] is False


def test_small_kalshi_bankroll_limits_size_without_overcommitting():
    rows = [_row(1000), _row(1015), _row(1100, positive=False)]
    result = simulate(
        rows,
        threshold_ms=10,
        kalshi_bankroll=Decimal("10"),
        trade_cap=Decimal("500"),
    )
    assert result["trades_taken"] == 1
    assert Decimal(result["peak_kalshi_capital_locked_dollars"]) <= Decimal("10")


def test_capital_is_not_reused_before_release():
    # Two fresh price levels in the same contract. A tiny Kalshi bankroll should
    # prevent the second position from reusing capital locked by the first.
    first = _row(1000, quantity="100")
    first_later = _row(1015, quantity="100")
    gap = _row(1020, positive=False)
    second = _row(1030, quantity="100")
    second["kalshi_yes_ask"] = "0.34"
    second["gemini_yes_bid"] = "0.59"
    second_later = dict(second)
    second_later["receive_ts_ms"] = 1045
    second_later["gemini_receive_ts_ms"] = 1042
    second_later["kalshi_receive_ts_ms"] = 1040

    result = simulate(
        [first, first_later, gap, second, second_later],
        threshold_ms=10,
        kalshi_bankroll=Decimal("20"),
        trade_cap=Decimal("100"),
    )
    assert Decimal(result["peak_kalshi_capital_locked_dollars"]) <= Decimal("20")
    assert result["skipped_insufficient_capital_or_cap"] >= 1 or result["partial_trades"] >= 1

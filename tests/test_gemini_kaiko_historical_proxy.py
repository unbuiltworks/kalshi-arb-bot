from decimal import Decimal

from src.gemini_kaiko_historical_proxy import _last_trade_at_or_before, _quantile


def test_last_trade_at_or_before_uses_latest_prior_trade():
    trades = [
        {"timestampms": 1000, "price": "100"},
        {"timestampms": 2000, "price": "101"},
        {"timestampms": 3000, "price": "102"},
    ]
    price, ts = _last_trade_at_or_before(trades, 2500)
    assert price == Decimal("101")
    assert ts == 2000


def test_last_trade_at_or_before_fails_closed_without_prior_trade():
    price, ts = _last_trade_at_or_before(
        [{"timestampms": 3000, "price": "102"}], 2500
    )
    assert price is None
    assert ts is None


def test_quantile_interpolates():
    assert _quantile([1.0, 3.0], 0.5) == 2.0

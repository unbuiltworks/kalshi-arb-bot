"""Kalshi adapter: side mapping, price normalization, tolerant parsing."""

import pytest

from src.census.core import DOWN, UP
from src.census.kalshi import parse_trade


def _trade(**overrides):
    record = {
        "trade_id": "t1",
        "ticker": "KXBTC15M-XXXX",
        "created_time": "2026-08-29T21:01:22Z",
        "taker_side": "yes",
        "count": 25,
        "yes_price": 62,
        "no_price": 38,
    }
    record.update(overrides)
    return record


class TestSideMapping:
    def test_yes_taker_feeds_the_makers_down_inventory(self):
        t = parse_trade(_trade(taker_side="yes"))
        assert t.maker_side == DOWN
        assert t.acquire_price == pytest.approx(0.38)

    def test_no_taker_feeds_the_makers_up_inventory(self):
        t = parse_trade(_trade(taker_side="no"))
        assert t.maker_side == UP
        assert t.acquire_price == pytest.approx(0.62)


class TestPriceNormalization:
    def test_integer_cents(self):
        t = parse_trade(_trade(yes_price=62, no_price=38))
        assert (t.up_price, t.down_price) == (pytest.approx(0.62), pytest.approx(0.38))

    def test_dollar_string_fields_take_precedence(self):
        t = parse_trade(_trade(yes_price_dollars="0.625", no_price_dollars="0.375"))
        assert t.up_price == pytest.approx(0.625)

    def test_missing_no_price_is_complemented(self):
        t = parse_trade(_trade(no_price=None))
        assert t.down_price == pytest.approx(0.38)

    def test_timestamps_iso_and_epoch(self):
        assert parse_trade(_trade(created_time="2026-08-29T21:01:22Z").copy()).ts == 1788037282
        assert parse_trade(_trade(created_time=1788037282)).ts == 1788037282


class TestTolerance:
    @pytest.mark.parametrize("overrides", [
        {"taker_side": "maybe"},
        {"count": 0},
        {"count": "not a number"},
        {"created_time": None},
        {"yes_price": None, "no_price": None},
    ])
    def test_unmappable_trades_return_none(self, overrides):
        assert parse_trade(_trade(**overrides)) is None

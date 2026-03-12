"""Tests for the spread scanner with synthetic orderbook data."""

import pytest

from src.orderbook import OrderBook
from src.spread_scanner import ArbSignal, SpreadScanner


def _make_book(ticker: str, yes_bids: list, no_bids: list) -> OrderBook:
    book = OrderBook(ticker)
    book.apply_snapshot(yes_bids=yes_bids, no_bids=no_bids)
    return book


class TestSpreadScanner:
    @pytest.mark.asyncio
    async def test_no_signal_normal_spread(self):
        """Normal market where arb cost > 100 should never signal."""
        scanner = SpreadScanner(min_edge_cents=1, min_depth=1, order_size=10)
        signals: list[ArbSignal] = []
        scanner.on_signal(lambda s: signals.append(s))

        # yes_bid=40, no_bid=50
        # yes_ask=100-50=50, no_ask=100-40=60 → cost=110 (no arb)
        book = _make_book("T1", yes_bids=[[40, 20]], no_bids=[[50, 20]])
        await scanner.scan("T1", book)
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_signal_on_arb(self):
        """When arb cost < 100 (minus fees), should emit signal."""
        scanner = SpreadScanner(
            min_edge_cents=1, min_depth=1, order_size=10, spread_flare_pct=200,
        )
        signals: list[ArbSignal] = []
        scanner.on_signal(lambda s: signals.append(s))

        # yes_bid=60, no_bid=55
        # yes_ask=100-55=45, no_ask=100-60=40 → cost=85 per contract
        # payout=100, raw edge=15 cents/contract before fees
        book = _make_book("T1", yes_bids=[[60, 20]], no_bids=[[55, 20]])
        await scanner.scan("T1", book)
        assert len(signals) == 1
        sig = signals[0]
        assert sig.market_ticker == "T1"
        assert sig.yes_ask_cents == 45
        assert sig.no_ask_cents == 40
        assert sig.quantity == 10
        assert sig.edge_cents > 0

    @pytest.mark.asyncio
    async def test_skips_insufficient_depth(self):
        scanner = SpreadScanner(
            min_edge_cents=1, min_depth=50, order_size=10, spread_flare_pct=200,
        )
        signals: list[ArbSignal] = []
        scanner.on_signal(lambda s: signals.append(s))

        # Arb exists but only 5 contracts deep
        book = _make_book("T1", yes_bids=[[60, 5]], no_bids=[[55, 5]])
        await scanner.scan("T1", book)
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_edge_below_threshold(self):
        scanner = SpreadScanner(
            min_edge_cents=999, min_depth=1, order_size=10, spread_flare_pct=200,
        )
        signals: list[ArbSignal] = []
        scanner.on_signal(lambda s: signals.append(s))

        book = _make_book("T1", yes_bids=[[60, 20]], no_bids=[[55, 20]])
        await scanner.scan("T1", book)
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_quantity_capped_by_order_size(self):
        scanner = SpreadScanner(
            min_edge_cents=1, min_depth=1, order_size=5, spread_flare_pct=200,
        )
        signals: list[ArbSignal] = []
        scanner.on_signal(lambda s: signals.append(s))

        book = _make_book("T1", yes_bids=[[60, 100]], no_bids=[[55, 100]])
        await scanner.scan("T1", book)
        assert len(signals) == 1
        assert signals[0].quantity == 5

    @pytest.mark.asyncio
    async def test_quantity_capped_by_depth(self):
        scanner = SpreadScanner(
            min_edge_cents=1, min_depth=1, order_size=100, spread_flare_pct=200,
        )
        signals: list[ArbSignal] = []
        scanner.on_signal(lambda s: signals.append(s))

        book = _make_book("T1", yes_bids=[[60, 7]], no_bids=[[55, 20]])
        await scanner.scan("T1", book)
        assert len(signals) == 1
        assert signals[0].quantity == 7

    @pytest.mark.asyncio
    async def test_picks_better_maker_side(self):
        scanner = SpreadScanner(
            min_edge_cents=1, min_depth=1, order_size=10, spread_flare_pct=200,
        )
        signals: list[ArbSignal] = []
        scanner.on_signal(lambda s: signals.append(s))

        # Asymmetric: yes_bid=80, no_bid=90
        # yes_ask=10, no_ask=20 → cost=30
        book = _make_book("T1", yes_bids=[[80, 20]], no_bids=[[90, 20]])
        await scanner.scan("T1", book)
        assert len(signals) == 1
        assert signals[0].maker_side in ("yes", "no")

    @pytest.mark.asyncio
    async def test_empty_book_no_crash(self):
        scanner = SpreadScanner()
        book = OrderBook("T1")
        await scanner.scan("T1", book)

    @pytest.mark.asyncio
    async def test_one_side_empty_no_crash(self):
        scanner = SpreadScanner()
        book = OrderBook("T1")
        book.apply_snapshot(yes_bids=[[50, 10]], no_bids=[])
        await scanner.scan("T1", book)

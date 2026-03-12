"""Tests for orderbook state management."""

import pytest

from src.orderbook import OrderBook, OrderBookManager


class TestOrderBook:
    def test_empty_book(self):
        book = OrderBook("TEST-TICKER")
        assert book.best_yes_bid() is None
        assert book.best_no_bid() is None
        assert book.best_yes_ask() is None
        assert book.best_no_ask() is None
        assert book.spread_cents() is None
        assert book.arb_cost_cents() is None
        assert book.arb_depth() == 0
        assert not book.initialized

    def test_snapshot(self):
        book = OrderBook("TEST-TICKER")
        # yes_bids: someone buying YES at 55 (20 contracts) and 50 (30 contracts)
        # no_bids: someone buying NO at 60 (15 contracts) and 55 (25 contracts)
        book.apply_snapshot(
            yes_bids=[[55, 20], [50, 30]],
            no_bids=[[60, 15], [55, 25]],
        )
        assert book.initialized
        assert book.best_yes_bid() == 55
        assert book.best_no_bid() == 60
        # yes_ask = 100 - best_no_bid = 100 - 60 = 40
        assert book.best_yes_ask() == 40
        # no_ask = 100 - best_yes_bid = 100 - 55 = 45
        assert book.best_no_ask() == 45
        assert book.depth_at_yes_ask() == 15  # depth at best no_bid
        assert book.depth_at_no_ask() == 20   # depth at best yes_bid

    def test_arb_cost_no_arb(self):
        book = OrderBook("TEST-TICKER")
        # yes_bid=40, no_bid=50
        # yes_ask = 100-50 = 50, no_ask = 100-40 = 60
        # arb_cost = 50 + 60 = 110 (no arb)
        book.apply_snapshot(yes_bids=[[40, 10]], no_bids=[[50, 10]])
        assert book.arb_cost_cents() == 110

    def test_arb_cost_arb_exists(self):
        book = OrderBook("TEST-TICKER")
        # yes_bid=60, no_bid=55
        # yes_ask = 100-55 = 45, no_ask = 100-60 = 40
        # arb_cost = 45 + 40 = 85 (arb exists! 15 cents per contract before fees)
        book.apply_snapshot(yes_bids=[[60, 10]], no_bids=[[55, 10]])
        assert book.arb_cost_cents() == 85

    def test_delta_updates_levels(self):
        book = OrderBook("TEST-TICKER")
        book.apply_snapshot(yes_bids=[[55, 20]], no_bids=[[60, 15]])

        # New yes_bid at 57, no_bid at 60 updated to 10
        book.apply_delta(yes_bids=[[57, 5]], no_bids=[[60, 10]])
        assert book.best_yes_bid() == 57
        assert book.depth_at_price(57, "yes_bid") == 5
        assert book.depth_at_price(55, "yes_bid") == 20  # still there
        assert book.no_bids[60] == 10  # updated

    def test_delta_removes_level(self):
        book = OrderBook("TEST-TICKER")
        book.apply_snapshot(yes_bids=[[55, 20], [50, 30]], no_bids=[[60, 15]])

        # Remove the 55 yes_bid (qty=0)
        book.apply_delta(yes_bids=[[55, 0]], no_bids=[])
        assert book.best_yes_bid() == 50
        assert 55 not in book.yes_bids

    def test_snapshot_replaces_book(self):
        book = OrderBook("TEST-TICKER")
        book.apply_snapshot(yes_bids=[[55, 20]], no_bids=[[60, 15]])
        # Second snapshot should fully replace
        book.apply_snapshot(yes_bids=[[70, 5]], no_bids=[[75, 8]])
        assert book.best_yes_bid() == 70
        assert book.best_no_bid() == 75
        assert 55 not in book.yes_bids
        assert 60 not in book.no_bids

    def test_snapshot_ignores_zero_qty(self):
        book = OrderBook("TEST-TICKER")
        book.apply_snapshot(yes_bids=[[55, 0], [50, 10]], no_bids=[[60, 0]])
        assert book.best_yes_bid() == 50
        assert 55 not in book.yes_bids
        assert book.best_no_bid() is None

    def test_depth_at_price_missing(self):
        book = OrderBook("TEST-TICKER")
        book.apply_snapshot(yes_bids=[[55, 20]], no_bids=[[60, 15]])
        assert book.depth_at_price(99, "yes_bid") == 0
        assert book.depth_at_price(1, "no_bid") == 0

    def test_arb_depth(self):
        book = OrderBook("TEST-TICKER")
        # yes_bid=60 x20, no_bid=55 x15
        book.apply_snapshot(yes_bids=[[60, 20]], no_bids=[[55, 15]])
        # arb_depth = min(depth_at_yes_ask, depth_at_no_ask) = min(15, 20)
        assert book.arb_depth() == 15


class TestOrderBookManager:
    @pytest.mark.asyncio
    async def test_handle_snapshot_fp_format(self):
        """Test with the actual WS format (dollars_fp)."""
        mgr = OrderBookManager()
        msg = {
            "type": "orderbook_snapshot",
            "msg": {
                "market_ticker": "TEST-MKT",
                "yes_dollars_fp": [["0.5500", "20.00"], ["0.5000", "30.00"]],
                "no_dollars_fp": [["0.6000", "15.00"], ["0.5500", "25.00"]],
            },
        }
        await mgr.handle_snapshot(msg)
        book = mgr.get("TEST-MKT")
        assert book is not None
        assert book.initialized
        assert book.best_yes_bid() == 55  # converted to cents
        assert book.best_no_bid() == 60

    @pytest.mark.asyncio
    async def test_handle_snapshot_legacy_format(self):
        """Test with legacy integer format."""
        mgr = OrderBookManager()
        msg = {
            "type": "orderbook_snapshot",
            "msg": {
                "market_ticker": "TEST-MKT",
                "yes": [[55, 20], [50, 30]],
                "no": [[60, 15]],
            },
        }
        await mgr.handle_snapshot(msg)
        book = mgr.get("TEST-MKT")
        assert book is not None
        assert book.best_yes_bid() == 55

    @pytest.mark.asyncio
    async def test_handle_delta(self):
        mgr = OrderBookManager()
        # First send snapshot
        await mgr.handle_snapshot({
            "type": "orderbook_snapshot",
            "msg": {
                "market_ticker": "T1",
                "yes": [[50, 10]],
                "no": [[60, 10]],
            },
        })
        # Then delta
        await mgr.handle_delta({
            "type": "orderbook_delta",
            "msg": {
                "market_ticker": "T1",
                "yes_dollars_fp": [["0.5200", "5.00"]],
            },
        })
        book = mgr.get("T1")
        assert book.best_yes_bid() == 52

    @pytest.mark.asyncio
    async def test_delta_before_snapshot_ignored(self):
        mgr = OrderBookManager()
        await mgr.handle_delta({
            "type": "orderbook_delta",
            "msg": {"market_ticker": "T1", "yes": [[52, 5]]},
        })
        book = mgr.get("T1")
        assert book is not None
        assert not book.initialized
        assert book.best_yes_bid() is None

    @pytest.mark.asyncio
    async def test_book_update_callback(self):
        mgr = OrderBookManager()
        updates = []

        async def on_update(ticker, book):
            updates.append((ticker, book.best_yes_bid()))

        mgr.on_book_update(on_update)

        await mgr.handle_snapshot({
            "type": "orderbook_snapshot",
            "msg": {"market_ticker": "T1", "yes": [[50, 10]], "no": [[60, 10]]},
        })
        assert len(updates) == 1
        assert updates[0] == ("T1", 50)

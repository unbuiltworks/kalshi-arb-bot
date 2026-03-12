"""
Local orderbook state maintained from WebSocket snapshots and deltas.

Kalshi's WS sends two bid books:
    yes_dollars_fp: people offering to buy YES at various prices
    no_dollars_fp:  people offering to buy NO at various prices

To BUY YES, you take against someone's NO bid:
    YES ask = 100 - best NO bid price  (in cents)

To BUY NO, you take against someone's YES bid:
    NO ask = 100 - best YES bid price  (in cents)

For arb: total = yes_ask + no_ask = (100 - best_no_bid) + (100 - best_yes_bid)
If total + fees < 100, arb exists.

Each market gets its own OrderBook instance. The OrderBookManager holds
all books and wires into KalshiWS callbacks.
"""

import logging
from typing import Any

logger = logging.getLogger("kalshi_arb.orderbook")


class OrderBook:
    """
    Single-market orderbook tracking YES bids and NO bids.

    Price levels are stored as dicts mapping price_cents -> quantity.
    A quantity of 0 means the level is empty (removed).
    """

    def __init__(self, market_ticker: str):
        self.market_ticker = market_ticker
        # {price_cents: quantity} — only non-zero levels kept
        self.yes_bids: dict[int, int] = {}  # people wanting to buy YES
        self.no_bids: dict[int, int] = {}   # people wanting to buy NO
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ------------------------------------------------------------------
    # Snapshot / delta application
    # ------------------------------------------------------------------

    def apply_snapshot(
        self, yes_bids: list[list], no_bids: list[list],
    ) -> None:
        """
        Replace the entire book from a snapshot message.

        Args:
            yes_bids: [[price_cents, quantity], ...] orders to buy YES
            no_bids:  [[price_cents, quantity], ...] orders to buy NO
        """
        self.yes_bids = {int(p): int(q) for p, q in yes_bids if int(q) > 0}
        self.no_bids = {int(p): int(q) for p, q in no_bids if int(q) > 0}
        self._initialized = True
        logger.debug(
            "%s snapshot: %d yes_bid levels, %d no_bid levels",
            self.market_ticker,
            len(self.yes_bids),
            len(self.no_bids),
        )

    def apply_delta(
        self, yes_bids: list[list], no_bids: list[list],
    ) -> None:
        """Apply incremental updates. Quantity of 0 removes the level."""
        for p, q in yes_bids:
            p, q = int(p), int(q)
            if q > 0:
                self.yes_bids[p] = q
            else:
                self.yes_bids.pop(p, None)

        for p, q in no_bids:
            p, q = int(p), int(q)
            if q > 0:
                self.no_bids[p] = q
            else:
                self.no_bids.pop(p, None)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def best_yes_bid(self) -> int | None:
        """Highest YES bid price in cents."""
        return max(self.yes_bids) if self.yes_bids else None

    def best_no_bid(self) -> int | None:
        """Highest NO bid price in cents."""
        return max(self.no_bids) if self.no_bids else None

    def best_yes_ask(self) -> int | None:
        """
        Cheapest price to buy YES, in cents.

        Buying YES = taking against a NO bid.
        YES ask = 100 - best_no_bid.
        """
        no_bid = self.best_no_bid()
        return (100 - no_bid) if no_bid is not None else None

    def best_no_ask(self) -> int | None:
        """
        Cheapest price to buy NO, in cents.

        Buying NO = taking against a YES bid.
        NO ask = 100 - best_yes_bid.
        """
        yes_bid = self.best_yes_bid()
        return (100 - yes_bid) if yes_bid is not None else None

    def depth_at_yes_ask(self) -> int:
        """Quantity available at the best YES ask (= depth at best NO bid)."""
        no_bid = self.best_no_bid()
        return self.no_bids.get(no_bid, 0) if no_bid is not None else 0

    def depth_at_no_ask(self) -> int:
        """Quantity available at the best NO ask (= depth at best YES bid)."""
        yes_bid = self.best_yes_bid()
        return self.yes_bids.get(yes_bid, 0) if yes_bid is not None else 0

    def depth_at_price(self, price_cents: int, side: str) -> int:
        """
        Quantity at a specific price level.

        Args:
            price_cents: price in cents
            side: "yes_bid" or "no_bid"
        """
        book = self.yes_bids if side == "yes_bid" else self.no_bids
        return book.get(price_cents, 0)

    def spread_cents(self) -> int | None:
        """YES spread in cents (yes_ask - yes_bid), or None if either side empty."""
        ask = self.best_yes_ask()
        bid = self.best_yes_bid()
        if ask is None or bid is None:
            return None
        return ask - bid

    def arb_cost_cents(self) -> int | None:
        """
        Cost to buy 1 YES + 1 NO in cents (before fees).

        = yes_ask + no_ask
        = (100 - best_no_bid) + (100 - best_yes_bid)

        Values < 100 mean arb exists (before fees).
        """
        yes_ask = self.best_yes_ask()
        no_ask = self.best_no_ask()
        if yes_ask is None or no_ask is None:
            return None
        return yes_ask + no_ask

    def arb_depth(self) -> int:
        """Max contracts tradeable on both sides of the arb."""
        return min(self.depth_at_yes_ask(), self.depth_at_no_ask())

    def __repr__(self) -> str:
        return (
            f"OrderBook({self.market_ticker} "
            f"yes_bid={self.best_yes_bid()} yes_ask={self.best_yes_ask()} "
            f"no_bid={self.best_no_bid()} no_ask={self.best_no_ask()} "
            f"arb_cost={self.arb_cost_cents()})"
        )


class OrderBookManager:
    """
    Manages orderbooks for all tracked markets.

    Wires directly into KalshiWS callbacks:
        ws.on_orderbook_snapshot(manager.handle_snapshot)
        ws.on_orderbook_delta(manager.handle_delta)
    """

    def __init__(self):
        self._books: dict[str, OrderBook] = {}
        self._on_book_update: list[Any] = []

    def on_book_update(self, callback) -> None:
        """Register a callback(market_ticker, orderbook) called after every update."""
        self._on_book_update.append(callback)

    def get(self, market_ticker: str) -> OrderBook | None:
        return self._books.get(market_ticker)

    def get_or_create(self, market_ticker: str) -> OrderBook:
        if market_ticker not in self._books:
            self._books[market_ticker] = OrderBook(market_ticker)
        return self._books[market_ticker]

    def all_books(self) -> dict[str, OrderBook]:
        return dict(self._books)

    def remove(self, market_ticker: str) -> None:
        self._books.pop(market_ticker, None)

    async def _notify(self, market_ticker: str) -> None:
        book = self._books.get(market_ticker)
        if book is None:
            return
        for cb in self._on_book_update:
            try:
                await cb(market_ticker, book)
            except Exception:
                logger.exception("Book update callback error for %s", market_ticker)

    # ------------------------------------------------------------------
    # WS message parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_book_side(data: dict[str, Any], key_fp: str, key_legacy: str) -> list[list]:
        """
        Extract book levels from a WS message, handling both formats:

        New format (dollars_fp):  [["0.0500", "40000.00"], ...]
          → converted to [[5, 40000], ...] (cents, int qty)

        Legacy format (integer):  [[5, 40000], ...]
          → passed through as-is
        """
        levels = data.get(key_fp, [])
        if levels:
            result = []
            for price_str, qty_str in levels:
                price_cents = round(float(price_str) * 100)
                qty = int(float(qty_str))
                result.append([price_cents, qty])
            return result

        return data.get(key_legacy, [])

    # ------------------------------------------------------------------
    # WS message handlers
    # ------------------------------------------------------------------

    async def handle_snapshot(self, msg: dict[str, Any]) -> None:
        """
        Handle an orderbook_snapshot from WS.

        Format:
            {"type": "orderbook_snapshot", "msg": {
                "market_ticker": "...",
                "yes_dollars_fp": [["0.05","100.00"], ...],
                "no_dollars_fp": [["0.95","50.00"], ...]
            }}
        """
        snapshot = msg.get("msg", msg)
        ticker = snapshot.get("market_ticker", "")
        if not ticker:
            logger.warning("Snapshot missing market_ticker: %s", msg)
            return

        yes_bids = self._parse_book_side(snapshot, "yes_dollars_fp", "yes")
        no_bids = self._parse_book_side(snapshot, "no_dollars_fp", "no")

        book = self.get_or_create(ticker)
        book.apply_snapshot(yes_bids, no_bids)
        logger.info("%s snapshot applied: %s", ticker, book)
        await self._notify(ticker)

    async def handle_delta(self, msg: dict[str, Any]) -> None:
        """Handle an orderbook_delta from WS."""
        delta = msg.get("msg", msg)
        ticker = delta.get("market_ticker", "")
        if not ticker:
            logger.warning("Delta missing market_ticker: %s", msg)
            return

        yes_bids = self._parse_book_side(delta, "yes_dollars_fp", "yes")
        no_bids = self._parse_book_side(delta, "no_dollars_fp", "no")

        book = self.get_or_create(ticker)
        if not book.initialized:
            logger.debug("Delta for uninitialized book %s, waiting for snapshot", ticker)
            return

        book.apply_delta(yes_bids, no_bids)
        await self._notify(ticker)

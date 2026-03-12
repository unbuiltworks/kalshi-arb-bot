"""
Spread scanner — detects arb opportunities on every orderbook update.

Wires into OrderBookManager.on_book_update(). On each update, checks if
buying YES + NO (after fees) costs less than the guaranteed $1.00 payout.

When an opportunity is found, it picks the optimal maker side (whichever
yields higher edge) and emits a signal to registered callbacks.
"""

import logging
import time
from dataclasses import dataclass
from collections.abc import Callable, Coroutine
from typing import Any

from config.settings import (
    MIN_DEPTH,
    MIN_EDGE_CENTS,
    ORDER_SIZE,
    SPREAD_FLARE_THRESHOLD_PCT,
    LOG_EVERY_SCAN,
    LOG_OPPORTUNITIES,
)
from src.fee_calc import compute_edge_cents, total_arb_fees_cents
from src.orderbook import OrderBook

logger = logging.getLogger("kalshi_arb.scanner")

SignalHandler = Callable[["ArbSignal"], Coroutine[Any, Any, None]]


@dataclass(frozen=True)
class ArbSignal:
    """Describes a detected arb opportunity."""

    market_ticker: str
    yes_ask_cents: int
    no_ask_cents: int
    quantity: int
    maker_side: str        # "yes" or "no"
    edge_cents: int        # total profit in cents for `quantity` contracts
    fees_cents: int        # total fees for both legs
    timestamp_ms: int


class SpreadScanner:
    """
    Scans orderbook updates for arb opportunities.

    Usage:
        scanner = SpreadScanner()
        scanner.on_signal(my_handler)
        book_manager.on_book_update(scanner.scan)
    """

    def __init__(
        self,
        min_edge_cents: int = MIN_EDGE_CENTS,
        min_depth: int = MIN_DEPTH,
        order_size: int = ORDER_SIZE,
        spread_flare_pct: float = SPREAD_FLARE_THRESHOLD_PCT,
    ):
        self._min_edge = min_edge_cents
        self._min_depth = min_depth
        self._order_size = order_size
        self._spread_flare_pct = spread_flare_pct
        self._signal_handlers: list[SignalHandler] = []

    def on_signal(self, handler: SignalHandler) -> None:
        """Register an async callback for when an arb signal is detected."""
        self._signal_handlers.append(handler)

    async def scan(self, market_ticker: str, book: OrderBook) -> None:
        """
        Check a single market for arb. Called on every book update.

        Args:
            market_ticker: the ticker that was updated
            book: the updated OrderBook
        """
        yes_ask = book.best_yes_ask()
        no_ask = book.best_no_ask()

        if yes_ask is None or no_ask is None:
            return

        # --- Filter: spread flare ---
        yes_bid = book.best_yes_bid()
        mid = (yes_ask + (yes_bid or yes_ask)) / 2.0
        if mid > 0 and yes_bid is not None:
            spread_pct = ((yes_ask - yes_bid) / mid) * 100
            if spread_pct > self._spread_flare_pct:
                if LOG_EVERY_SCAN:
                    logger.debug(
                        "%s spread flare %.1f%% > %.1f%%, skipping",
                        market_ticker, spread_pct, self._spread_flare_pct,
                    )
                return

        # --- Filter: depth ---
        available = book.arb_depth()
        if available < self._min_depth:
            if LOG_EVERY_SCAN:
                logger.debug(
                    "%s depth %d < %d, skipping",
                    market_ticker, available, self._min_depth,
                )
            return

        quantity = min(available, self._order_size)

        # --- Compute edge for both maker sides, pick the better one ---
        best_signal = None
        for maker_side in ("yes", "no"):
            edge = compute_edge_cents(quantity, yes_ask, no_ask, market_ticker, maker_side)
            if edge >= self._min_edge:
                fees = total_arb_fees_cents(quantity, yes_ask, no_ask, market_ticker, maker_side)
                if best_signal is None or edge > best_signal.edge_cents:
                    best_signal = ArbSignal(
                        market_ticker=market_ticker,
                        yes_ask_cents=yes_ask,
                        no_ask_cents=no_ask,
                        quantity=quantity,
                        maker_side=maker_side,
                        edge_cents=edge,
                        fees_cents=fees,
                        timestamp_ms=int(time.time() * 1000),
                    )

        if best_signal is None:
            if LOG_EVERY_SCAN:
                logger.debug(
                    "%s no edge: yes_ask=%d no_ask=%d qty=%d",
                    market_ticker, yes_ask, no_ask, quantity,
                )
            return

        # --- Emit signal ---
        if LOG_OPPORTUNITIES:
            logger.info(
                "ARB %s | yes_ask=%d no_ask=%d qty=%d maker=%s edge=%dc fees=%dc",
                best_signal.market_ticker,
                best_signal.yes_ask_cents,
                best_signal.no_ask_cents,
                best_signal.quantity,
                best_signal.maker_side,
                best_signal.edge_cents,
                best_signal.fees_cents,
            )

        for handler in self._signal_handlers:
            try:
                await handler(best_signal)
            except Exception:
                logger.exception("Signal handler error for %s", market_ticker)

"""Kalshi MLB prop hot books: websocket discovery with exact REST revalidation.

The authenticated orderbook_delta websocket drives candidate discovery. The
second read of an unchanged candidate generation in the canary's deliberate
35-150 ms revalidation window is upgraded to a direct depth=1 REST read.

This is read-only market data. It contains no order-entry functionality.
"""
from __future__ import annotations

from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src.eventing_books import EventingKalshiCrossBooks
from src.kalshi_cross_book_v2 import TwoSidedQuote
from src.strict_mlb_prop_snapshot import KALSHI, kalshi_top_book

logger = logging.getLogger("kalshi_arb.kalshi_prop_ws_exact_books")
D = Decimal
REVALIDATE_MIN_MS = 35
REVALIDATE_MAX_MS = 150


class KalshiPropWsExactBooks(EventingKalshiCrossBooks):
    """WS-driven top of book plus candidate-only exact REST second look."""

    def __init__(self, tickers: list[str] | tuple[str, ...], *, max_queue: int = 20000, **kwargs: Any) -> None:
        super().__init__(tickers, max_queue=max_queue, **kwargs)
        self._http: httpx.AsyncClient | None = None
        # ticker -> (WS generation received_ms, monotonic access time)
        self._first_access: dict[str, tuple[int, float]] = {}
        self.exact_book_requests = 0
        self.rate_limits = 0

    async def exact_quote(self, ticker: str) -> TwoSidedQuote | None:
        """Read the live depth=1 REST book without mutating the WS reconstruction."""
        if self._http is None:
            return None
        response = await self._http.get(
            f"{KALSHI}/markets/{ticker}/orderbook",
            params={"depth": 1},
        )
        self.exact_book_requests += 1
        if response.status_code == 429:
            self.rate_limits += 1
        if response.status_code != 200:
            return None

        body = response.json() if response.content else {}
        book = kalshi_top_book(body)
        if book is None:
            return None
        received_ms = int(time.time() * 1000)
        logger.info("KALSHI_PROP_WS_EXACT_REVALIDATION ticker=%s", ticker)
        return TwoSidedQuote(
            ticker=ticker,
            yes_ask=book.yes_ask,
            yes_depth=book.yes_depth,
            no_ask=book.no_ask,
            no_depth=book.no_depth,
            received_ms=received_ms,
            exchange_ts_ms=None,
            seq=None,
        )

    async def quote(self, ticker: str, *, max_age_ms: int = 30000) -> TwoSidedQuote | None:
        """Use WS for discovery; upgrade the canary's unchanged 50 ms second read."""
        cached = await super().quote(ticker, max_age_ms=max_age_ms)
        if cached is None:
            self._first_access.pop(ticker, None)
            return None

        now_mono = time.monotonic()
        marker = self._first_access.get(ticker)
        if marker is not None and marker[0] == cached.received_ms:
            elapsed_ms = (now_mono - marker[1]) * 1000.0
            if REVALIDATE_MIN_MS <= elapsed_ms <= REVALIDATE_MAX_MS:
                exact = await self.exact_quote(ticker)
                self._first_access.pop(ticker, None)
                return exact
            if elapsed_ms > REVALIDATE_MAX_MS:
                self._first_access[ticker] = (cached.received_ms, now_mono)
            return cached

        self._first_access[ticker] = (cached.received_ms, now_mono)
        return cached

    async def run(self) -> None:
        self._http = httpx.AsyncClient(timeout=5.0)
        logger.warning(
            "KALSHI_PROP_BOOK_TRANSPORT %s",
            json.dumps(
                {
                    "mode": "AUTHENTICATED_WS_DELTA_PLUS_CANDIDATE_EXACT_REST",
                    "tickers": len(self.tickers),
                    "exact_revalidation_window_ms": [REVALIDATE_MIN_MS, REVALIDATE_MAX_MS],
                    "read_only": True,
                    "order_entry": False,
                },
                separators=(",", ":"),
            ),
        )
        try:
            await super().run()
        finally:
            if self._http is not None:
                await self._http.aclose()
                self._http = None

    async def close(self) -> None:
        await super().close()
        if self._http is not None:
            await self._http.aclose()
            self._http = None

"""Event notifications for the read-only cross-exchange order-book stores.

These wrappers add bounded, non-blocking queues around the existing Polymarket and
Kalshi book implementations.  They do not contain any order-entry functionality.
The event-driven scanner can therefore wake up on a book mutation instead of
polling every N milliseconds.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any

from src.kalshi_cross_book_v2 import KalshiCrossBooks
from src.polymarket_us_market_ws import PolyBookStore


@dataclass(frozen=True)
class BookUpdate:
    venue: str
    market_id: str
    received_ms: int
    mono_ns: int
    kind: str


class _UpdateMixin:
    def _init_updates(self, maxsize: int = 20000) -> None:
        self.updates: asyncio.Queue[BookUpdate] = asyncio.Queue(maxsize=maxsize)
        self.dropped_updates = 0

    def _notify(self, venue: str, market_id: str, kind: str) -> None:
        event = BookUpdate(
            venue=venue,
            market_id=market_id,
            received_ms=int(time.time() * 1000),
            mono_ns=time.perf_counter_ns(),
            kind=kind,
        )
        try:
            self.updates.put_nowait(event)
        except asyncio.QueueFull:
            # Never let diagnostics backpressure the market-data websocket. Drop the
            # oldest wake-up; the book itself still contains the newest state.
            try:
                self.updates.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped_updates += 1
            try:
                self.updates.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped_updates += 1


class EventingPolyBookStore(_UpdateMixin, PolyBookStore):
    def __init__(self, slugs: list[str] | tuple[str, ...], *, max_queue: int = 20000) -> None:
        PolyBookStore.__init__(self, slugs)
        self._init_updates(max_queue)
        self.notifications_enabled = True

    def replace_from_book(self, slug: str, book: dict[str, Any], *, notify: bool = True) -> None:
        super().replace_from_book(slug, book)
        if notify and self.notifications_enabled:
            self._notify("polymarket", slug, "snapshot")

    def apply_incremental(self, slug: str, changes: dict[str, Any]) -> None:
        super().apply_incremental(slug, changes)
        if self.notifications_enabled:
            self._notify("polymarket", slug, "delta")


class EventingKalshiCrossBooks(_UpdateMixin, KalshiCrossBooks):
    def __init__(self, *args: Any, max_queue: int = 20000, **kwargs: Any) -> None:
        KalshiCrossBooks.__init__(self, *args, **kwargs)
        self._init_updates(max_queue)

    async def apply_snapshot(self, msg: dict[str, Any]) -> None:
        await super().apply_snapshot(msg)
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker", ""))
        if ticker in self.tickers:
            self._notify("kalshi", ticker, "snapshot")

    async def apply_delta(self, msg: dict[str, Any]) -> None:
        await super().apply_delta(msg)
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker", ""))
        if ticker in self.tickers:
            self._notify("kalshi", ticker, "delta")

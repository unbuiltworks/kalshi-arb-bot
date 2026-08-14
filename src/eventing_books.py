"""Event notifications for the read-only cross-exchange order-book stores.

These wrappers add bounded, non-blocking queues around the hardened v2 Polymarket
and Kalshi book implementations. They contain no order-entry functionality.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any

from src.kalshi_cross_book_v2 import KalshiCrossBooks
from src.polymarket_kalshi_shadow_v2 import PolyBookStore


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
            # Never backpressure either market-data websocket. The book still holds
            # the newest state even when an old wake-up is discarded.
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
    def __init__(self, *, max_queue: int = 20000) -> None:
        PolyBookStore.__init__(self)
        self._init_updates(max_queue)
        self.notifications_enabled = True

    def ingest(self, payload: Any, *, source: str) -> bool:
        # v2 replaces the immutable PolyQuote object on every accepted update. Track
        # object identities, not millisecond timestamps: two websocket messages can
        # legitimately arrive inside the same millisecond and both must wake the
        # event-driven scanner.
        before = {slug: id(quote) for slug, quote in self._quotes.items()}
        ok = super().ingest(payload, source=source)
        if not ok or not self.notifications_enabled:
            return ok
        changed = [
            slug for slug, quote in self._quotes.items()
            if before.get(slug) != id(quote)
        ]
        for slug in changed:
            self._notify("polymarket", slug, "ws" if source == "ws" else "rest")
        return ok


class EventingKalshiCrossBooks(_UpdateMixin, KalshiCrossBooks):
    def __init__(self, *args: Any, max_queue: int = 20000, **kwargs: Any) -> None:
        KalshiCrossBooks.__init__(self, *args, **kwargs)
        self._init_updates(max_queue)

    async def apply_snapshot(self, msg: dict[str, Any]) -> None:
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker", ""))
        await super().apply_snapshot(msg)
        if ticker in self.tickers and ticker in self._initialized:
            self._notify("kalshi", ticker, "snapshot")

    async def apply_delta(self, msg: dict[str, Any]) -> None:
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker", ""))
        was_initialized = ticker in self._initialized
        await super().apply_delta(msg)
        if ticker in self.tickers and was_initialized and ticker in self._initialized:
            self._notify("kalshi", ticker, "delta")

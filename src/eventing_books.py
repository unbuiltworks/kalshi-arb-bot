"""Event notifications for the read-only cross-exchange order-book stores.

These wrappers add bounded, non-blocking queues around the hardened v2 Polymarket
and Kalshi book implementations. They contain no order-entry functionality.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import time
from typing import Any

from src.kalshi_cross_book_v2 import KalshiCrossBooks
from src.polymarket_kalshi_shadow_v2 import PolyBookStore

logger = logging.getLogger("kalshi_arb.eventing_books")


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

        # Raw WebSocket telemetry is deliberately separate from PolyBookStore's
        # ``ws_updates`` counter. The base store increments only when a message
        # contains a valid, executable two-sided book. These counters tell us whether
        # a missing cached book means "never received" or "received but unusable".
        self.raw_ws_messages = 0
        self.raw_ws_rejected_messages = 0
        self.raw_ws_seen_slugs: set[str] = set()
        self.raw_ws_rejected_slugs: set[str] = set()
        self.last_raw_ws_message_ms = 0
        self._last_raw_diag_ms = 0

    @staticmethod
    def _payload_slug(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        data = payload.get("marketData")
        if isinstance(data, dict):
            payload = data
        elif isinstance(payload.get("market_data"), dict):
            payload = payload["market_data"]
        return str(payload.get("marketSlug") or payload.get("market_slug") or "")

    def _maybe_log_raw_diag(self, now_ms: int) -> None:
        if now_ms - self._last_raw_diag_ms < 10_000:
            return
        self._last_raw_diag_ms = now_ms
        logger.info(
            "POLY_HOT_RAW %s",
            json.dumps(
                {
                    "raw_messages": self.raw_ws_messages,
                    "raw_seen_markets": len(self.raw_ws_seen_slugs),
                    "raw_rejected_messages": self.raw_ws_rejected_messages,
                    "raw_ever_rejected_markets": len(self.raw_ws_rejected_slugs),
                    "two_sided_stored_markets": self.quote_count,
                    "accepted_ws_updates": self.ws_updates,
                    "raw_ws_age_ms": max(0, now_ms - self.last_raw_ws_message_ms),
                },
                separators=(",", ":"),
            ),
        )

    def ingest(self, payload: Any, *, source: str) -> bool:
        # The old implementation copied and rescanned every cached quote on each WS
        # update. The message already identifies its market, so compare only that
        # quote object: O(1) work rather than O(number_of_subscribed_markets).
        slug = self._payload_slug(payload)
        before = id(self._quotes[slug]) if slug in self._quotes else None

        now_ms = int(time.time() * 1000)
        if source == "ws":
            self.raw_ws_messages += 1
            self.last_raw_ws_message_ms = now_ms
            if slug:
                self.raw_ws_seen_slugs.add(slug)

        ok = super().ingest(payload, source=source)

        if source == "ws" and not ok:
            self.raw_ws_rejected_messages += 1
            if slug:
                self.raw_ws_rejected_slugs.add(slug)
        if source == "ws":
            self._maybe_log_raw_diag(now_ms)

        if not ok or not self.notifications_enabled or not slug:
            return ok

        quote = self._quotes.get(slug)
        if quote is not None and before != id(quote):
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

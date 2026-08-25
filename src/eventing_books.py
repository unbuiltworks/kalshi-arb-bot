"""Event notifications for the read-only cross-exchange order-book stores.

These wrappers add bounded, non-blocking queues around the hardened v2 Polymarket
and Kalshi book implementations. They contain no order-entry functionality.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from config.settings import REST_URL
from src.auth import auth_headers
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
        self.transport = "ws"
        self.rest_batch_requests = 0
        self.rest_batch_errors = 0
        self.rest_batch_snapshots = 0

    async def run(self) -> None:
        if os.getenv("KALSHI_REST_BOOKS_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}:
            await self._run_rest_batch()
            return
        await super().run()

    async def _fetch_rest_chunk(self, http: httpx.AsyncClient, tickers: tuple[str, ...]) -> list[dict[str, Any]]:
        endpoint = f"{REST_URL}/markets/orderbooks"
        headers = auth_headers(
            self.private_key,
            "GET",
            urlparse(endpoint).path,
            self.api_key_id,
        )
        response = await http.get(
            endpoint,
            params=[("tickers", ticker) for ticker in tickers],
            headers=headers,
        )
        self.rest_batch_requests += 1
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("orderbooks") or []
        return [row for row in rows if isinstance(row, dict)]

    async def _run_rest_batch(self) -> None:
        self._running = True
        self.transport = "rest_batch"
        interval_ms = max(50, int(os.getenv("KALSHI_REST_BOOKS_INTERVAL_MS", "100")))
        chunks = tuple(
            tuple(self.tickers[index:index + 100])
            for index in range(0, len(self.tickers), 100)
        )
        logger.warning(
            "KALSHI_REST_BATCH_FALLBACK %s",
            json.dumps(
                {
                    "tickers": len(self.tickers),
                    "chunks": len(chunks),
                    "interval_ms": interval_ms,
                    "transport": "REST_SNAPSHOT_NOT_WS_LIFETIME",
                    "read_only": True,
                    "order_entry": False,
                },
                separators=(",", ":"),
            ),
        )
        backoff = 0.25
        async with httpx.AsyncClient(timeout=5.0) as http:
            while self._running:
                cycle_start = time.perf_counter()
                try:
                    batches = await asyncio.gather(
                        *(self._fetch_rest_chunk(http, chunk) for chunk in chunks)
                    )
                    for rows in batches:
                        for row in rows:
                            ticker = str(row.get("ticker") or "")
                            orderbook = row.get("orderbook_fp") or row.get("orderbook") or {}
                            if ticker not in self._yes_bids or not isinstance(orderbook, dict):
                                continue
                            await self.apply_snapshot({
                                "msg": {
                                    "market_ticker": ticker,
                                    "yes_dollars": orderbook.get("yes_dollars") or orderbook.get("yes_dollars_fp") or [],
                                    "no_dollars": orderbook.get("no_dollars") or orderbook.get("no_dollars_fp") or [],
                                }
                            })
                            self.rest_batch_snapshots += 1
                    backoff = 0.25
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.rest_batch_errors += 1
                    logger.warning(
                        "KALSHI_REST_BATCH_ERROR type=%s retry_s=%.2f requests=%d errors=%d",
                        type(exc).__name__,
                        backoff,
                        self.rest_batch_requests,
                        self.rest_batch_errors,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(5.0, backoff * 2)
                    continue

                elapsed = time.perf_counter() - cycle_start
                sleep_s = max(0.0, interval_ms / 1000.0 - elapsed)
                if sleep_s:
                    await asyncio.sleep(sleep_s)

    async def apply_snapshot(self, msg: dict[str, Any]) -> None:
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker", ""))
        await super().apply_snapshot(msg)
        if ticker in self.tickers and ticker in self._initialized:
            self._notify("kalshi", ticker, "rest_batch" if self.transport == "rest_batch" else "snapshot")

    async def apply_delta(self, msg: dict[str, Any]) -> None:
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker", ""))
        was_initialized = ticker in self._initialized
        await super().apply_delta(msg)
        if ticker in self.tickers and was_initialized and ticker in self._initialized:
            self._notify("kalshi", ticker, "delta")

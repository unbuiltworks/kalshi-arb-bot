"""Read-only fixed-point Kalshi order book feed for cross-market scanning.

Maintains YES bid depth for configured tickers and derives the executable NO ask
as 1 - best YES bid. This module never submits orders.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

import websockets
from websockets.asyncio.client import ClientConnection

from config.settings import API_KEY_ID, WS_PING_INTERVAL_S, WS_URL
from src.auth import auth_headers, load_private_key

logger = logging.getLogger("kalshi_arb.cross_kalshi_ws")


@dataclass(frozen=True)
class BestNoAsk:
    ticker: str
    price_dollars: Decimal
    count: Decimal
    exchange_ts_ms: int | None
    received_ms: int
    seq: int | None


class LiveKalshiBooks:
    """Maintain current YES-bid books and expose executable NO asks."""

    def __init__(
        self,
        tickers: list[str] | tuple[str, ...],
        *,
        ws_url: str | None = None,
        api_key_id: str | None = None,
        private_key_path: str | None = None,
    ) -> None:
        self.tickers = tuple(dict.fromkeys(tickers))
        if not self.tickers:
            raise ValueError("at least one Kalshi ticker is required")
        self.ws_url = ws_url or WS_URL
        self.api_key_id = api_key_id or API_KEY_ID
        self.private_key = load_private_key(private_key_path)
        self._yes_bids: dict[str, dict[Decimal, Decimal]] = {ticker: {} for ticker in self.tickers}
        self._received_ms: dict[str, int] = {}
        self._exchange_ts_ms: dict[str, int | None] = {}
        self._initialized: set[str] = set()
        self._last_seq: int | None = None
        self._lock = asyncio.Lock()
        self._running = False
        self._ws: ClientConnection | None = None

    def _headers(self) -> dict[str, str]:
        path = urlparse(self.ws_url).path
        return auth_headers(self.private_key, "GET", path, self.api_key_id)

    async def run(self) -> None:
        self._running = True
        backoff = 0.25
        while self._running:
            try:
                await self._run_once()
                backoff = 0.25
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Kalshi book stream reset: %s", exc)
            if not self._running:
                return
            await self._invalidate_all()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    async def _run_once(self) -> None:
        async with websockets.connect(
            self.ws_url,
            additional_headers=self._headers(),
            ping_interval=WS_PING_INTERVAL_S,
        ) as ws:
            self._ws = ws
            await ws.send(json.dumps({
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": list(self.tickers),
                    "use_yes_price": True,
                },
            }))
            logger.info("Kalshi WS subscribed to %d matched ticker(s)", len(self.tickers))
            async for raw in ws:
                if not self._running:
                    break
                msg = json.loads(raw)
                msg_type = msg.get("type")
                if msg_type in {"orderbook_snapshot", "orderbook_delta"}:
                    self._validate_sequence(msg)
                if msg_type == "orderbook_snapshot":
                    await self.apply_snapshot(msg)
                elif msg_type == "orderbook_delta":
                    await self.apply_delta(msg)
                elif msg_type == "error":
                    raise RuntimeError(f"Kalshi WS error: {msg.get('msg')}")

    def _validate_sequence(self, msg: dict[str, Any]) -> None:
        seq = _int_or_none(msg.get("seq"))
        if seq is None:
            return
        if self._last_seq is not None and seq != self._last_seq + 1:
            raise RuntimeError(f"orderbook sequence gap: previous={self._last_seq} current={seq}")
        self._last_seq = seq

    async def close(self) -> None:
        self._running = False
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _invalidate_all(self) -> None:
        async with self._lock:
            self._initialized.clear()
            self._received_ms.clear()
            self._exchange_ts_ms.clear()
            self._last_seq = None
            for book in self._yes_bids.values():
                book.clear()

    @staticmethod
    def _snapshot_yes_levels(body: dict[str, Any]) -> list[list[str]]:
        return body.get("yes_dollars_fp") or body.get("yes_dollars") or []

    async def apply_snapshot(self, msg: dict[str, Any]) -> None:
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker", ""))
        if ticker not in self._yes_bids:
            return
        levels: dict[Decimal, Decimal] = {}
        for price_raw, count_raw in self._snapshot_yes_levels(body):
            price = Decimal(str(price_raw))
            count = Decimal(str(count_raw))
            if count > 0:
                levels[price] = count
        received = int(time.time() * 1000)
        async with self._lock:
            self._yes_bids[ticker] = levels
            self._received_ms[ticker] = received
            self._exchange_ts_ms[ticker] = _int_or_none(body.get("ts_ms"))
            self._initialized.add(ticker)

    async def apply_delta(self, msg: dict[str, Any]) -> None:
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker", ""))
        if ticker not in self._yes_bids:
            return
        async with self._lock:
            if ticker not in self._initialized:
                return
            if str(body.get("side", "")).lower() == "yes":
                price_raw = body.get("price_dollars")
                delta_raw = body.get("delta_fp")
                if price_raw is None or delta_raw is None:
                    raise RuntimeError(f"malformed orderbook_delta for {ticker}: {body}")
                price = Decimal(str(price_raw))
                delta = Decimal(str(delta_raw))
                new_count = self._yes_bids[ticker].get(price, Decimal(0)) + delta
                if new_count > 0:
                    self._yes_bids[ticker][price] = new_count
                else:
                    self._yes_bids[ticker].pop(price, None)
            self._received_ms[ticker] = int(time.time() * 1000)
            self._exchange_ts_ms[ticker] = _int_or_none(body.get("ts_ms"))

    async def best_no_ask(self, ticker: str, *, max_age_ms: int | None = None) -> BestNoAsk | None:
        now_ms = int(time.time() * 1000)
        async with self._lock:
            if ticker not in self._initialized:
                return None
            received = self._received_ms.get(ticker)
            if received is None or (max_age_ms is not None and now_ms - received > max_age_ms):
                return None
            book = self._yes_bids.get(ticker) or {}
            if not book:
                return None
            best_yes_bid = max(book)
            return BestNoAsk(
                ticker=ticker,
                price_dollars=Decimal("1") - best_yes_bid,
                count=book[best_yes_bid],
                exchange_ts_ms=self._exchange_ts_ms.get(ticker),
                received_ms=received,
                seq=self._last_seq,
            )


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

"""Persistent public Gemini prediction-market depth feed for the q10 hot path.

Unlike ``fresh_top`` in the legacy q1 canary, this does not open a new WebSocket
for every reprice decision.  It keeps one `{symbol}@depth5@100ms` subscription
alive and serves the latest validated top of book. No credentials or order entry.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
import time
from typing import Any, Mapping

import websockets

from src import gemini_short_crypto_btc5m_maker_canary_q1 as legacy

WS = "wss://ws.gemini.com"
MAX_BOOK_AGE_SECONDS = 0.75


class PredictionDepthFeed:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._top: tuple[D,D,D,D] | None = None
        self._last_update: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None
        self.frames = 0

    async def start(self, timeout: float = 3.0) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        deadline = time.monotonic() + timeout
        while self._top is None and self._error is None and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        if self._top is None:
            await self.close()
            if self._error is not None:
                raise RuntimeError("no fresh Gemini BTC5m depth") from self._error
            raise RuntimeError("no fresh Gemini BTC5m depth")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def latest(self) -> tuple[D,D,D,D]:
        if self._error is not None or self._top is None or self._last_update is None:
            raise RuntimeError("no fresh Gemini BTC5m depth")
        if time.monotonic() - self._last_update > MAX_BOOK_AGE_SECONDS:
            raise RuntimeError("no fresh Gemini BTC5m depth")
        return self._top

    async def _run(self) -> None:
        try:
            async with websockets.connect(WS, open_timeout=3.0, close_timeout=1.0,
                                          ping_interval=10, ping_timeout=5, max_queue=5000) as ws:
                await ws.send(json.dumps({
                    "id": f"q10-depth-{self.symbol}",
                    "method": "SUBSCRIBE",
                    "params": [f"{self.symbol}@depth5@100ms"],
                }))
                while True:
                    raw = await ws.recv()
                    try:
                        payload: Any = json.loads(raw)
                    except Exception:
                        continue
                    top = legacy._top(payload)
                    if top is None:
                        continue
                    self._top = top
                    self._last_update = time.monotonic()
                    self.frames += 1
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc


class DepthManager:
    """Lazy one-symbol manager suitable for monkeypatching legacy ``fresh_top``."""
    def __init__(self) -> None:
        self.feed: PredictionDepthFeed | None = None
        self.symbol: str | None = None

    async def top(self, symbol: str, timeout: float = 4.0) -> tuple[D,D,D,D]:
        if self.feed is None or self.symbol != symbol:
            await self.close()
            self.feed = PredictionDepthFeed(symbol)
            self.symbol = symbol
            await self.feed.start(timeout=min(timeout, 3.0))
        try:
            return self.feed.latest()
        except RuntimeError:
            # One controlled reconnect is cheaper and safer than repeatedly opening
            # sockets on every decision tick.
            await self.feed.close()
            self.feed = PredictionDepthFeed(symbol)
            await self.feed.start(timeout=min(timeout, 3.0))
            return self.feed.latest()

    async def close(self) -> None:
        if self.feed is not None:
            await self.feed.close()
        self.feed = None
        self.symbol = None

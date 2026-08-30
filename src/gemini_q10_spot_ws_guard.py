"""Low-latency public BTC spot feed for q10 toxicity protection.

Subscribes to Gemini's public `BTCUSD@bookTicker` stream on wss://ws.gemini.com
and exposes the best-bid/ask midpoint.  No credentials or order entry.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
import time
from typing import Any

import websockets

WS = "wss://ws.gemini.com"
STREAM = "BTCUSD@bookTicker"


class GeminiSpotBookTicker:
    def __init__(self) -> None:
        self._mid: D | None = None
        self._last_update_mono: float | None = None
        self._connected = False
        self._error: BaseException | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.frames = 0

    async def start(self, *, startup_timeout: float = 3.0) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        deadline = time.monotonic() + startup_timeout
        while self._mid is None and self._error is None and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        if self._mid is None:
            await self.close()
            if self._error is not None:
                raise RuntimeError("Gemini BTC spot websocket failed") from self._error
            raise TimeoutError("Gemini BTC spot websocket produced no book ticker")

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._connected = False

    def latest(self) -> D:
        if self._error is not None:
            raise RuntimeError("Gemini BTC spot websocket unhealthy") from self._error
        if not self._connected or self._mid is None:
            raise RuntimeError("Gemini BTC spot websocket unavailable")
        return self._mid

    @property
    def last_update_age_ms(self) -> float | None:
        if self._last_update_mono is None:
            return None
        return (time.monotonic() - self._last_update_mono) * 1000.0

    async def _run(self) -> None:
        try:
            async with websockets.connect(WS, open_timeout=3.0, close_timeout=1.0,
                                          ping_interval=10, ping_timeout=5) as ws:
                self._connected = True
                await ws.send(json.dumps({"id":"spot-1","method":"SUBSCRIBE","params":[STREAM]}))
                while not self._stop.is_set():
                    raw = await ws.recv()
                    payload: Any = json.loads(raw)
                    if not isinstance(payload, dict):
                        continue
                    if "b" not in payload or "a" not in payload:
                        continue
                    bid = D(str(payload["b"])); ask = D(str(payload["a"]))
                    if bid <= 0 or ask <= 0 or ask < bid:
                        continue
                    self._mid = (bid + ask) / D("2")
                    self._last_update_mono = time.monotonic()
                    self.frames += 1
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc
        finally:
            self._connected = False


async def probe(duration_s: float = 2.0) -> dict[str, Any]:
    feed = GeminiSpotBookTicker()
    started = time.monotonic()
    await feed.start()
    first_ms = (time.monotonic() - started) * 1000.0
    first = str(feed.latest())
    await asyncio.sleep(duration_s)
    result = {
        "classification":"READ_ONLY_PUBLIC_GEMINI_SPOT_WS_PROBE",
        "order_entry":False,
        "credentials_loaded":False,
        "stream":STREAM,
        "first_price_ms":round(first_ms,3),
        "first_mid":first,
        "last_mid":str(feed.latest()),
        "frames":feed.frames,
        "last_update_age_ms":None if feed.last_update_age_ms is None else round(feed.last_update_age_ms,3),
    }
    await feed.close()
    return result

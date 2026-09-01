"""Persistent in-memory Gemini prediction-market top-of-book cache.

Opening a new websocket for every decision costs roughly a network round trip plus
subscription setup. This cache keeps one depth subscription hot for a campaign and
provides fail-closed freshness checks to the execution path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal as D
import json
import time
from typing import Any

import websockets

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base


@dataclass(frozen=True)
class BookSnapshot:
    bid: D
    bid_size: D
    ask: D
    ask_size: D
    age_ms: float
    received_ns: int
    sequence: int

    def as_top(self) -> tuple[D, D, D, D]:
        return self.bid, self.bid_size, self.ask, self.ask_size


class GeminiTopCache:
    def __init__(self, symbol: str, *, stale_ms: int = 750) -> None:
        self.symbol = symbol
        self.stale_ms = int(stale_ms)
        self._top: tuple[D, D, D, D] | None = None
        self._received_ns: int | None = None
        self._sequence = 0
        self._updated = asyncio.Event()
        self.connected = False
        self.last_error: str | None = None

    def snapshot(self, *, now_ns: int | None = None) -> BookSnapshot | None:
        if self._top is None or self._received_ns is None:
            return None
        now = time.monotonic_ns() if now_ns is None else int(now_ns)
        age_ms = max(0.0, (now - self._received_ns) / 1_000_000)
        return BookSnapshot(*self._top, age_ms, self._received_ns, self._sequence)

    async def wait_fresh(self, *, timeout: float = 2.0, max_age_ms: int | None = None) -> BookSnapshot:
        max_age = self.stale_ms if max_age_ms is None else int(max_age_ms)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snap = self.snapshot()
            if snap is not None and snap.age_ms <= max_age:
                return snap
            self._updated.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._updated.wait(), timeout=min(remaining, 0.25))
            except asyncio.TimeoutError:
                pass
        snap = self.snapshot()
        age = None if snap is None else round(snap.age_ms, 3)
        raise RuntimeError(f"no fresh persistent Gemini depth: symbol={self.symbol} age_ms={age}")

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 0.10
        while not stop.is_set():
            try:
                async with websockets.connect(
                    base.WS,
                    open_timeout=3.0,
                    ping_interval=15,
                    ping_timeout=8,
                    close_timeout=1,
                    max_queue=5000,
                ) as ws:
                    await ws.send(json.dumps({
                        "id": f"prod-hot-{self.symbol}",
                        "method": "SUBSCRIBE",
                        "params": [f"{self.symbol}@depth5@100ms"],
                    }))
                    self.connected = True
                    self.last_error = None
                    backoff = 0.10
                    while not stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        try:
                            data: Any = json.loads(raw)
                        except Exception:
                            continue
                        top = base._top(data)
                        if top is None:
                            continue
                        self._top = top
                        self._received_ns = time.monotonic_ns()
                        self._sequence += 1
                        self._updated.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.last_error = type(exc).__name__
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(2.0, backoff * 2)
        self.connected = False

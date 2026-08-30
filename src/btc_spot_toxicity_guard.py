"""Low-latency public BTC spot guard for Gemini 5m prediction-market making.

The live executor uses this only as a defensive input. It never creates a positive
trading signal by itself. If the feed is stale or BTC has moved too quickly over a
short rolling window, entry quotes should be pulled/not posted. Post-fill completion
is governed separately by the pair-profit/risk policy.

Coinbase Advanced Trade's public ticker websocket is used because it publishes a
message on matches and requires no credentials. The guard is deliberately tiny and
keeps all hot-path state in memory.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from decimal import Decimal as D
import json
import time
from typing import Any

import websockets

COINBASE_WS = "wss://advanced-trade-ws.coinbase.com"


@dataclass(frozen=True)
class SpotDecision:
    ready: bool
    stale: bool
    dangerous: bool
    latest_price: D | None
    age_ms: float | None
    move_bps: D | None
    samples: int
    reason: str


class BtcSpotGuard:
    def __init__(self, *, window_ms: int = 2000, stale_ms: int = 1500,
                 pull_move_bps: D = D("5"), max_samples: int = 256) -> None:
        if window_ms <= 0 or stale_ms <= 0:
            raise ValueError("window_ms and stale_ms must be positive")
        self.window_ms = int(window_ms)
        self.stale_ms = int(stale_ms)
        self.pull_move_bps = D(str(pull_move_bps))
        self.samples: deque[tuple[int, D]] = deque(maxlen=max_samples)
        self.last_sequence: int | None = None
        self.last_error: str | None = None
        self.connected = False

    def record(self, price: D | str | float, *, now_ns: int | None = None) -> None:
        p = D(str(price))
        if p <= 0:
            return
        ts = time.monotonic_ns() if now_ns is None else int(now_ns)
        self.samples.append((ts, p))
        cutoff = ts - self.window_ms * 1_000_000
        while len(self.samples) > 1 and self.samples[0][0] < cutoff:
            self.samples.popleft()

    def decision(self, *, now_ns: int | None = None) -> SpotDecision:
        now = time.monotonic_ns() if now_ns is None else int(now_ns)
        if not self.samples:
            return SpotDecision(False, True, True, None, None, None, 0, "NO_SPOT_SAMPLE")
        latest_ts, latest = self.samples[-1]
        age_ms = max(0.0, (now - latest_ts) / 1_000_000)
        stale = age_ms > self.stale_ms
        cutoff = now - self.window_ms * 1_000_000
        in_window = [(ts, p) for ts, p in self.samples if ts >= cutoff]
        if not in_window:
            in_window = [self.samples[-1]]
        first = in_window[0][1]
        move_bps = ((latest / first) - D("1")) * D("10000") if first > 0 else D("0")
        dangerous = stale or abs(move_bps) >= self.pull_move_bps
        if stale:
            reason = "STALE_SPOT"
        elif abs(move_bps) >= self.pull_move_bps:
            reason = "FAST_SPOT_MOVE"
        else:
            reason = "CALM"
        return SpotDecision(
            ready=not stale,
            stale=stale,
            dangerous=dangerous,
            latest_price=latest,
            age_ms=age_ms,
            move_bps=move_bps,
            samples=len(in_window),
            reason=reason,
        )

    @staticmethod
    def _ticker_prices(payload: dict[str, Any]) -> list[D]:
        if payload.get("channel") != "ticker":
            return []
        out: list[D] = []
        for event in payload.get("events") or []:
            if not isinstance(event, dict):
                continue
            for ticker in event.get("tickers") or []:
                if not isinstance(ticker, dict) or ticker.get("product_id") != "BTC-USD":
                    continue
                try:
                    out.append(D(str(ticker["price"])))
                except Exception:
                    continue
        return out

    async def run_coinbase(self, stop: asyncio.Event) -> None:
        """Maintain the public ticker feed until stop is set.

        Reconnects with bounded backoff. Feed failures make `decision()` stale and
        therefore conservative; callers never infer calmness from a disconnected
        socket.
        """
        backoff = 0.25
        while not stop.is_set():
            try:
                async with websockets.connect(
                    COINBASE_WS,
                    ping_interval=15,
                    ping_timeout=10,
                    close_timeout=1,
                    max_queue=64,
                ) as ws:
                    self.connected = True
                    self.last_error = None
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channel": "ticker",
                    }))
                    await ws.send(json.dumps({"type": "subscribe", "channel": "heartbeats"}))
                    backoff = 0.25
                    while not stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        payload = json.loads(raw)
                        seq = payload.get("sequence_num")
                        if isinstance(seq, int):
                            self.last_sequence = seq
                        for price in self._ticker_prices(payload):
                            self.record(price)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.last_error = type(exc).__name__
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(5.0, backoff * 2)
        self.connected = False

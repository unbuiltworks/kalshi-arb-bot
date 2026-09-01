"""Shadow-only heartbeat-aware BTC spot toxicity guard.

Coinbase's ticker channel only emits on matches. Treating a quiet ticker as a dead
feed causes unnecessary quote pulls even while websocket heartbeats prove the
connection is healthy. This shadow variant separates feed liveness from trade-price
updates while preserving the existing fast-move fail-closed behavior.

Not wired into live execution.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
import time

import websockets

from src.btc_spot_toxicity_guard import BtcSpotGuard, COINBASE_WS, SpotDecision


class BtcSpotGuardV2Shadow(BtcSpotGuard):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.last_rx_ns: int | None = None

    def record_liveness(self, *, now_ns: int | None = None) -> None:
        self.last_rx_ns = time.monotonic_ns() if now_ns is None else int(now_ns)

    def record(self, price, *, now_ns: int | None = None) -> None:
        ts = time.monotonic_ns() if now_ns is None else int(now_ns)
        self.record_liveness(now_ns=ts)
        super().record(price, now_ns=ts)

    def decision(self, *, now_ns: int | None = None) -> SpotDecision:
        now = time.monotonic_ns() if now_ns is None else int(now_ns)
        base = super().decision(now_ns=now)
        if not self.samples:
            return base

        # Once websocket liveness is being tracked, use any received message
        # (ticker or heartbeat) to decide whether the feed is stale. Quiet trading
        # must not masquerade as a dead connection.
        if self.last_rx_ns is None:
            return base

        feed_age_ms = max(0.0, (now - self.last_rx_ns) / 1_000_000)
        stale = feed_age_ms > self.stale_ms
        move_bps = base.move_bps
        fast = move_bps is not None and abs(move_bps) >= self.pull_move_bps
        dangerous = stale or fast
        if stale:
            reason = "STALE_SPOT_FEED"
        elif fast:
            reason = "FAST_SPOT_MOVE"
        else:
            reason = "CALM"
        return SpotDecision(
            ready=not stale,
            stale=stale,
            dangerous=dangerous,
            latest_price=base.latest_price,
            age_ms=feed_age_ms,
            move_bps=move_bps,
            samples=base.samples,
            reason=reason,
        )

    async def run_coinbase(self, stop: asyncio.Event) -> None:
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
                        self.record_liveness()
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

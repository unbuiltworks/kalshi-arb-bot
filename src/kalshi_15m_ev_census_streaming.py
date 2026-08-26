"""Streaming-spot wrapper for the read-only Kalshi BTC 15m EV census.

The underlying census is intentionally unchanged.  This wrapper replaces its
REST-polled spot loop with a dual public Coinbase/Kraken WebSocket composite so
sub-100ms opportunity survival is not measured against a ~250ms stale proxy.

Fail-closed rule: a SpotTape observation is emitted only when both venue prices
are fresh (<= 1s old).  The observation is the median of the two prices.  No
order-entry functionality exists here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import statistics
import time
from typing import Any

import websockets

from src import kalshi_15m_ev_census as census
from src.pk_crypto_ev_census import SpotTape

logger = logging.getLogger("kalshi_arb.kalshi_15m_ev_census.streaming_spot")

COINBASE_WS = "wss://advanced-trade-ws.coinbase.com"
KRAKEN_WS = "wss://ws.kraken.com/v2"
MAX_SOURCE_AGE_MS = 1000


class DualSpotComposite:
    def __init__(self, tape: SpotTape) -> None:
        self.tape = tape
        self.latest: dict[str, tuple[float, int]] = {}
        self.lock = asyncio.Lock()
        self.last_diag_mono = 0.0
        self.last_tape_ts_ms = 0

    async def update(self, source: str, price: float) -> None:
        if not math.isfinite(price) or price <= 0:
            return
        now_ms = int(time.time() * 1000)
        async with self.lock:
            self.latest[source] = (price, now_ms)
            cb = self.latest.get("coinbase")
            kr = self.latest.get("kraken")
            if cb is not None and kr is not None:
                cb_age = now_ms - cb[1]
                kr_age = now_ms - kr[1]
                if cb_age <= MAX_SOURCE_AGE_MS and kr_age <= MAX_SOURCE_AGE_MS:
                    composite = statistics.median((cb[0], kr[0]))
                    ts_ms = max(now_ms, self.last_tape_ts_ms + 1)
                    self.tape.add(ts_ms, composite)
                    self.last_tape_ts_ms = ts_ms

            now_mono = time.monotonic()
            if now_mono - self.last_diag_mono >= 10.0:
                self.last_diag_mono = now_mono
                cb = self.latest.get("coinbase")
                kr = self.latest.get("kraken")
                basis_bp = None
                if cb is not None and kr is not None and cb[0] > 0:
                    basis_bp = (kr[0] / cb[0] - 1.0) * 10000.0
                logger.info("K15_STREAMING_SPOT %s", json.dumps({
                    "coinbase_price": None if cb is None else cb[0],
                    "coinbase_age_ms": None if cb is None else now_ms - cb[1],
                    "kraken_price": None if kr is None else kr[0],
                    "kraken_age_ms": None if kr is None else now_ms - kr[1],
                    "kraken_minus_coinbase_bp": basis_bp,
                    "composite_samples": len(self.tape.samples),
                    "freshness_limit_ms": MAX_SOURCE_AGE_MS,
                    "classification": "DUAL_PUBLIC_WS_MEDIAN_PROXY_FAIL_CLOSED_IF_EITHER_STALE",
                }, separators=(",", ":")))


async def _coinbase_loop(composite: DualSpotComposite) -> None:
    backoff = 0.25
    while True:
        try:
            async with websockets.connect(COINBASE_WS, ping_interval=20) as ws:
                await ws.send(json.dumps({
                    "type": "subscribe",
                    "product_ids": ["BTC-USD"],
                    "channel": "ticker",
                }))
                await ws.send(json.dumps({
                    "type": "subscribe",
                    "product_ids": ["BTC-USD"],
                    "channel": "heartbeats",
                }))
                logger.info("K15_SPOT_COINBASE_WS_CONNECTED")
                backoff = 0.25
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("channel") != "ticker":
                        continue
                    for event in msg.get("events") or []:
                        for row in event.get("tickers") or []:
                            if str(row.get("product_id") or "") != "BTC-USD":
                                continue
                            try:
                                await composite.update("coinbase", float(row["price"]))
                            except (KeyError, TypeError, ValueError):
                                continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("K15_SPOT_COINBASE_WS_RESET error=%s retry_s=%.2f", exc, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


async def _kraken_loop(composite: DualSpotComposite) -> None:
    backoff = 0.25
    while True:
        try:
            async with websockets.connect(KRAKEN_WS, ping_interval=20) as ws:
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "params": {
                        "channel": "ticker",
                        "symbol": ["BTC/USD"],
                        "snapshot": True,
                    },
                }))
                logger.info("K15_SPOT_KRAKEN_WS_CONNECTED")
                backoff = 0.25
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("channel") != "ticker":
                        continue
                    for row in msg.get("data") or []:
                        if str(row.get("symbol") or "") != "BTC/USD":
                            continue
                        raw_price: Any = row.get("last")
                        if raw_price is None:
                            bid = row.get("bid")
                            ask = row.get("ask")
                            try:
                                raw_price = (float(bid) + float(ask)) / 2.0
                            except (TypeError, ValueError):
                                raw_price = None
                        try:
                            if raw_price is not None:
                                await composite.update("kraken", float(raw_price))
                        except (TypeError, ValueError):
                            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("K15_SPOT_KRAKEN_WS_RESET error=%s retry_s=%.2f", exc, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


async def streaming_spot_loop(tape: SpotTape, *, poll_ms: int) -> None:
    del poll_ms  # API compatibility with the underlying census.
    composite = DualSpotComposite(tape)
    logger.info("K15_STREAMING_SPOT_START classification=DUAL_PUBLIC_WS_MEDIAN_PROXY")
    coinbase = asyncio.create_task(_coinbase_loop(composite))
    kraken = asyncio.create_task(_kraken_loop(composite))
    try:
        await asyncio.gather(coinbase, kraken)
    finally:
        coinbase.cancel()
        kraken.cancel()
        await asyncio.gather(coinbase, kraken, return_exceptions=True)


def main() -> None:
    # The base module resolves this global at runtime inside run().
    census.spot_loop = streaming_spot_loop
    census.main()


if __name__ == "__main__":
    main()

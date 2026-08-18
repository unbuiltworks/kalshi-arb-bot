"""Shard Gemini MLB prediction bookTicker subscriptions for the read-only GK execution shadow.

Gemini closes a single subscription carrying the full MLB instrument universe.
This wrapper keeps the base execution-shadow logic unchanged but spreads symbols
across small public market-data WebSockets. No order transport is imported or
enabled here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import websockets

from src import three_venue_mlb_gk_exec_shadow as base
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker

logger = logging.getLogger("kalshi_arb.three_venue_mlb_gk_exec_shadow_sharded")
SHARD_SIZE = 8


async def _run_shard(store: base.GeminiBookTickerStore, symbols: list[str], shard_id: int, shard_count: int) -> None:
    wanted = {s.lower(): s for s in symbols}
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(
                GEMINI_WS,
                open_timeout=5.0,
                ping_interval=20,
                ping_timeout=20,
                max_size=4 * 1024 * 1024,
            ) as ws:
                await ws.send(json.dumps({
                    "id": f"mlb-gk-exec-{shard_id}",
                    "method": "SUBSCRIBE",
                    "params": [f"{s}@bookTicker" for s in symbols],
                }, separators=(",", ":")))
                connected = getattr(store, "_connected_shards", set())
                connected.add(shard_id)
                store._connected_shards = connected
                store.connected = len(connected) == shard_count
                backoff = 1.0
                logger.info("MLB_GK_EXEC_GEMINI_SHARD_CONNECTED %s", json.dumps({
                    "shard": shard_id,
                    "shard_count": shard_count,
                    "symbols": len(symbols),
                    "all_connected": store.connected,
                }, separators=(",", ":")))
                async for raw in ws:
                    received_ms = int(time.time() * 1000)
                    store.last_message_ms = received_ms
                    store.messages += 1
                    payload = json.loads(raw)
                    if payload.get("status") is not None:
                        if int(payload.get("status")) >= 400:
                            raise RuntimeError(f"Gemini subscription failed status={payload.get('status')}")
                        continue
                    raw_symbol = str(payload.get("s") or "")
                    symbol = wanted.get(raw_symbol.lower())
                    if symbol is None:
                        continue
                    try:
                        book = parse_gemini_book_ticker(payload, received_ms=received_ms)
                    except (KeyError, ValueError, TypeError):
                        continue
                    store.books[symbol] = book
                    store.book_updates += 1
                    store._notify(symbol, received_ms)
        except asyncio.CancelledError:
            connected = getattr(store, "_connected_shards", set())
            connected.discard(shard_id)
            store._connected_shards = connected
            store.connected = False
            raise
        except Exception as exc:
            connected = getattr(store, "_connected_shards", set())
            connected.discard(shard_id)
            store._connected_shards = connected
            store.connected = False
            logger.warning(
                "MLB_GK_EXEC_GEMINI_SHARD_RECONNECT shard=%s error_type=%s error=%s retry_s=%.1f",
                shard_id, type(exc).__name__, str(exc)[:180], backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2.0)


async def sharded_run(store: base.GeminiBookTickerStore) -> None:
    if not store.symbols:
        raise RuntimeError("no Gemini symbols to subscribe")
    shards = [store.symbols[i:i + SHARD_SIZE] for i in range(0, len(store.symbols), SHARD_SIZE)]
    store._connected_shards = set()
    logger.info("MLB_GK_EXEC_GEMINI_SHARDS %s", json.dumps({
        "symbols": len(store.symbols),
        "shard_size": SHARD_SIZE,
        "shards": len(shards),
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))
    tasks = [
        asyncio.create_task(_run_shard(store, symbols, idx, len(shards)), name=f"gemini_shard_{idx}")
        for idx, symbols in enumerate(shards)
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.connected = False


base.GeminiBookTickerStore.run = sharded_run

if __name__ == "__main__":
    base.main()

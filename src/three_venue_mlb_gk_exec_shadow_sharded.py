"""Adaptive Gemini MLB bookTicker subscriptions for the read-only GK execution shadow.

Gemini can reject a multi-symbol prediction-market subscription with status=400.
This wrapper starts with small batches but automatically bisects rejected batches
until each valid instrument is isolated. A bad symbol therefore cannot flap an
entire shard. It also emits an explicit read-only HOT_CANDIDATE event when the
base shadow sees a q=1 candidate that already satisfies the micro price/freshness
thresholds. Rule approval and live arming remain mandatory blockers; no order
transport is imported or enabled here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import websockets

from src import three_venue_mlb_gk_exec_shadow as base
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker

logger = logging.getLogger("kalshi_arb.three_venue_mlb_gk_exec_shadow_sharded")
SHARD_SIZE = 8


class SubscriptionRejected(RuntimeError):
    """Gemini rejected the requested subscription payload."""


def _split_symbols(symbols: list[str]) -> tuple[list[str], list[str]]:
    if len(symbols) < 2:
        return symbols, []
    mid = max(1, len(symbols) // 2)
    return symbols[:mid], symbols[mid:]


def _mark_connected(store: base.GeminiBookTickerStore, symbols: list[str]) -> None:
    connected = getattr(store, "_connected_symbols", set())
    connected.update(symbols)
    store._connected_symbols = connected
    store.connected = set(store.symbols).issubset(connected)


def _mark_disconnected(store: base.GeminiBookTickerStore, symbols: list[str]) -> None:
    connected = getattr(store, "_connected_symbols", set())
    for symbol in symbols:
        connected.discard(symbol)
    store._connected_symbols = connected
    store.connected = set(store.symbols).issubset(connected)


async def _run_leaf(store: base.GeminiBookTickerStore, symbols: list[str], shard_id: str) -> None:
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
                # Do not count the symbols connected until Gemini has accepted the
                # request or produced actual market data.
                accepted = False
                backoff = 1.0
                async for raw in ws:
                    received_ms = int(time.time() * 1000)
                    store.last_message_ms = received_ms
                    store.messages += 1
                    payload = json.loads(raw)
                    if payload.get("status") is not None:
                        status = int(payload.get("status"))
                        if status >= 400:
                            raise SubscriptionRejected(f"Gemini subscription failed status={status}")
                        accepted = True
                        _mark_connected(store, symbols)
                        continue
                    raw_symbol = str(payload.get("s") or "")
                    symbol = wanted.get(raw_symbol.lower())
                    if symbol is None:
                        continue
                    if not accepted:
                        accepted = True
                        _mark_connected(store, symbols)
                    try:
                        book = parse_gemini_book_ticker(payload, received_ms=received_ms)
                    except (KeyError, ValueError, TypeError):
                        continue
                    store.books[symbol] = book
                    store.book_updates += 1
                    store._notify(symbol, received_ms)
        except asyncio.CancelledError:
            _mark_disconnected(store, symbols)
            raise
        except SubscriptionRejected as exc:
            _mark_disconnected(store, symbols)
            if len(symbols) > 1:
                left, right = _split_symbols(symbols)
                logger.warning("MLB_GK_EXEC_GEMINI_ADAPTIVE_SPLIT %s", json.dumps({
                    "shard": shard_id,
                    "symbols": len(symbols),
                    "left": len(left),
                    "right": len(right),
                    "reason": str(exc),
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")))
                await asyncio.gather(
                    _run_leaf(store, left, shard_id + "L"),
                    _run_leaf(store, right, shard_id + "R"),
                )
                return
            logger.warning(
                "MLB_GK_EXEC_GEMINI_BAD_SYMBOL symbol=%s error=%s retry_s=%.1f",
                symbols[0], str(exc)[:180], backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2.0)
        except Exception as exc:
            _mark_disconnected(store, symbols)
            logger.warning(
                "MLB_GK_EXEC_GEMINI_SHARD_RECONNECT shard=%s symbols=%s error_type=%s error=%s retry_s=%.1f",
                shard_id, len(symbols), type(exc).__name__, str(exc)[:180], backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2.0)


async def sharded_run(store: base.GeminiBookTickerStore) -> None:
    if not store.symbols:
        raise RuntimeError("no Gemini symbols to subscribe")
    shards = [store.symbols[i:i + SHARD_SIZE] for i in range(0, len(store.symbols), SHARD_SIZE)]
    store._connected_symbols = set()
    logger.info("MLB_GK_EXEC_GEMINI_ADAPTIVE_SHARDS %s", json.dumps({
        "symbols": len(store.symbols),
        "initial_shard_size": SHARD_SIZE,
        "initial_shards": len(shards),
        "fallback": "BISECT_TO_SINGLE_SYMBOL",
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))
    tasks = [
        asyncio.create_task(_run_leaf(store, symbols, str(idx)), name=f"gemini_shard_{idx}")
        for idx, symbols in enumerate(shards)
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.connected = False


def _is_hot_payload(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    blockers = payload.get("blockers")
    if not isinstance(blockers, list):
        return False
    if "PAYLOAD_ECONOMICS_MISMATCH" in blockers:
        return False
    try:
        profit = float(payload.get("recomputed_payload_locked_profit", "0"))
        skew = int(payload.get("quote_skew_ms", 10**9))
        gemini_age = int(payload.get("gemini_age_ms", 10**9))
        kalshi_age = int(payload.get("kalshi_age_ms", 10**9))
    except (TypeError, ValueError):
        return False
    return profit >= 0.03 and skew <= 50 and gemini_age <= 100 and kalshi_age <= 100


_original_evaluate_key = base.evaluate_key


async def hot_funnel_evaluate_key(*args: Any, **kwargs: Any) -> None:
    active = kwargs.get("active")
    key = args[0] if args else kwargs.get("key")
    before_hot = False
    if isinstance(active, dict) and key in active:
        before_hot = bool(active[key].get("hot_candidate"))
    await _original_evaluate_key(*args, **kwargs)
    if not isinstance(active, dict) or key not in active:
        return
    payload = active[key]
    is_hot = _is_hot_payload(payload)
    payload["hot_candidate"] = is_hot
    if is_hot and not before_hot:
        remaining = [
            blocker for blocker in payload.get("blockers", [])
            if blocker not in {"QUOTE_SKEW_GT_50MS", "AFTER_FEE_PROFIT_LT_3C"}
        ]
        logger.info("MLB_GK_HOT_CANDIDATE %s", json.dumps({
            "settlement_key": payload.get("settlement_key"),
            "market_family": payload.get("market_family"),
            "gemini_symbol": payload.get("gemini_symbol"),
            "kalshi_ticker": payload.get("kalshi_ticker"),
            "direction": payload.get("direction"),
            "locked_profit": payload.get("recomputed_payload_locked_profit"),
            "net_cost": payload.get("net_cost"),
            "roi_on_cost": payload.get("roi_on_cost"),
            "quote_skew_ms": payload.get("quote_skew_ms"),
            "gemini_age_ms": payload.get("gemini_age_ms"),
            "kalshi_age_ms": payload.get("kalshi_age_ms"),
            "remaining_blockers": remaining,
            "execution_eligible": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":"), default=str))


base.GeminiBookTickerStore.run = sharded_run
base.evaluate_key = hot_funnel_evaluate_key

if __name__ == "__main__":
    base.main()

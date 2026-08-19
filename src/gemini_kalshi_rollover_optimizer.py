"""Latency-only rollover optimizer for the bounded Gemini/Kalshi live canary.

This module changes market-data transport only.  It does not change quantity, profit,
settlement, boundary, freshness, skew, depth, or execution gates.

Two live bottlenecks are addressed:
1. Kalshi rollover previously opened one additional authenticated WebSocket per asset,
   causing HTTP 429s when BTC/ETH/SOL/XRP prewarmed together.  Kalshi supports many
   orderbook subscriptions on one authenticated WebSocket, so current and next-window
   tickers now share one connection and are routed to the existing per-feed queues.
2. Exact Kalshi settlement thresholds were fetched serially after rollover.  The next
   window's metadata is now prefetched concurrently and atomically promoted when the
   quarter-hour changes.

All next-window events remain subject to the existing activation timestamp gate in
``gemini_kalshi_predictive_prewarmed_safe`` and cannot enter economics early.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx
import websockets

from src import gemini_kalshi_predictive_prewarmed as base
from src import gemini_kalshi_predictive_prewarmed_safe as prewarmed_safe
from src import gemini_kalshi_micro_live_readiness_20bp as readiness
from src.gemini_kalshi_predictive_hot import predicted_pair

hot = base.hot
basis = readiness.basis
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_rollover_optimizer")

# Give the shared socket enough time to receive the next-market snapshot before the
# boundary.  No extra Kalshi connection is created, so this no longer increases the
# WebSocket handshake rate.
PREWARM_LEAD_S = 5.0
TARGET_PREFETCH_LEAD_S = 60.0
TARGET_RETRY_S = 0.25


def _subscribe_payload(command_id: int, ticker: str) -> dict[str, Any]:
    return {
        "id": int(command_id),
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta"],
            "market_ticker": str(ticker),
            # Preserve the executor's validated legacy two-scale orderbook math.
            "use_yes_price": False,
        },
    }


def _unsubscribe_payload(command_id: int, sid: int) -> dict[str, Any]:
    return {
        "id": int(command_id),
        "cmd": "unsubscribe",
        "params": {"sids": [int(sid)]},
    }


def _snapshot_payload(command_id: int, sid: int, ticker: str) -> dict[str, Any]:
    return {
        "id": int(command_id),
        "cmd": "update_subscription",
        "params": {
            "sids": [int(sid)],
            "market_tickers": [str(ticker)],
            "action": "get_snapshot",
        },
    }


class SharedKalshiOrderbookHub:
    """One authenticated Kalshi socket routing many market books to existing feeds."""

    def __init__(self) -> None:
        self._targets: dict[str, list[tuple[asyncio.Queue, Any]]] = defaultdict(list)
        self._books: dict[str, Any] = {}
        self._sid_by_ticker: dict[str, int] = {}
        self._ticker_by_command: dict[int, str] = {}
        self._pending_tickers: set[str] = set()
        self._ws: Any | None = None
        self._task: asyncio.Task[Any] | None = None
        self._send_lock = asyncio.Lock()
        self._registry_lock = asyncio.Lock()
        self._next_command_id = 1000
        self._connected = asyncio.Event()

    def _command_id(self) -> int:
        self._next_command_id += 1
        return self._next_command_id

    async def register(self, ticker: str, queue: asyncio.Queue, stats: Any) -> None:
        ticker = str(ticker)
        async with self._registry_lock:
            target = (queue, stats)
            if target not in self._targets[ticker]:
                self._targets[ticker].append(target)
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run(), name="kalshi-shared-orderbook-hub")
        if self._connected.is_set():
            await self._ensure_subscribed(ticker)

    async def unregister(self, ticker: str, queue: asyncio.Queue, stats: Any) -> None:
        ticker = str(ticker)
        should_unsubscribe = False
        async with self._registry_lock:
            targets = self._targets.get(ticker, [])
            target = (queue, stats)
            if target in targets:
                targets.remove(target)
            if not targets:
                self._targets.pop(ticker, None)
                should_unsubscribe = True
        if should_unsubscribe:
            await self._unsubscribe(ticker)

    async def _send(self, payload: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            return
        async with self._send_lock:
            await ws.send(json.dumps(payload, separators=(",", ":")))

    async def _ensure_subscribed(self, ticker: str) -> None:
        if ticker not in self._targets:
            return
        if ticker in self._sid_by_ticker or ticker in self._pending_tickers:
            return
        command_id = self._command_id()
        self._ticker_by_command[command_id] = ticker
        self._pending_tickers.add(ticker)
        self._books[ticker] = hot.GuardedKalshiOrderBook(ticker)
        try:
            await self._send(_subscribe_payload(command_id, ticker))
        except Exception:
            self._ticker_by_command.pop(command_id, None)
            self._pending_tickers.discard(ticker)
            raise

    async def _unsubscribe(self, ticker: str) -> None:
        self._pending_tickers.discard(ticker)
        sid = self._sid_by_ticker.pop(ticker, None)
        self._books.pop(ticker, None)
        if sid is None or self._ws is None:
            return
        try:
            await self._send(_unsubscribe_payload(self._command_id(), sid))
        except Exception as exc:
            logger.warning(
                "CRYPTO_ROLLOVER_SHARED_KALSHI_UNSUBSCRIBE_ERROR ticker=%s type=%s error=%s",
                ticker,
                type(exc).__name__,
                str(exc)[:180],
            )

    async def _repair_snapshot(self, ticker: str) -> None:
        sid = self._sid_by_ticker.get(ticker)
        self._books[ticker] = hot.GuardedKalshiOrderBook(ticker)
        if sid is None:
            self._pending_tickers.discard(ticker)
            await self._ensure_subscribed(ticker)
            return
        await self._send(_snapshot_payload(self._command_id(), sid, ticker))

    async def _handle(self, payload: dict[str, Any]) -> None:
        typ = str(payload.get("type") or "")
        msg = payload.get("msg") or {}
        command_id = payload.get("id")

        if typ == "subscribed":
            try:
                cid = int(command_id)
            except (TypeError, ValueError):
                cid = -1
            ticker = self._ticker_by_command.pop(cid, None)
            if ticker is not None:
                self._pending_tickers.discard(ticker)
                sid_raw = payload.get("sid", msg.get("sid"))
                if sid_raw is not None:
                    self._sid_by_ticker[ticker] = int(sid_raw)
                logger.info(
                    "CRYPTO_ROLLOVER_SHARED_KALSHI_SUBSCRIBED %s",
                    json.dumps({
                        "ticker": ticker,
                        "sid": self._sid_by_ticker.get(ticker),
                        "active_market_subscriptions": len(self._targets),
                        "shared_websocket": True,
                        "order_entry": False,
                    }, separators=(",", ":")),
                )
            return

        if typ == "error":
            try:
                cid = int(command_id)
            except (TypeError, ValueError):
                cid = -1
            ticker = self._ticker_by_command.pop(cid, None)
            if ticker is not None:
                self._pending_tickers.discard(ticker)
            logger.warning(
                "CRYPTO_ROLLOVER_SHARED_KALSHI_COMMAND_ERROR ticker=%s error=%s",
                ticker,
                str(msg)[:220],
            )
            return

        if typ not in {"orderbook_snapshot", "orderbook_delta"}:
            return
        ticker = str(msg.get("market_ticker") or "")
        targets = list(self._targets.get(ticker, ()))
        if not ticker or not targets:
            return
        book = self._books.get(ticker)
        if book is None:
            book = hot.GuardedKalshiOrderBook(ticker)
            self._books[ticker] = book
        try:
            changed = book.apply(payload)
        except hot.SequenceGap:
            for _queue, stats in targets:
                stats.kalshi_sequence_gaps += 1
            logger.warning(
                "CRYPTO_ROLLOVER_SHARED_KALSHI_SEQUENCE_GAP ticker=%s action=get_snapshot",
                ticker,
            )
            await self._repair_snapshot(ticker)
            return
        if not changed:
            return
        receive_ms = int(time.time() * 1000)
        mono_ns = time.perf_counter_ns()
        top = book.top(receive_ms)
        if top is None:
            return
        for queue, stats in targets:
            stats.events += 1
            stats.kalshi_events += 1
            hot._enqueue(
                queue,
                ("kalshi", receive_ms, book.exchange_ts_ms, top, mono_ns),
                stats,
            )

    async def _run(self) -> None:
        private_key = hot.load_private_key()
        backoff = 0.25
        while True:
            try:
                headers = hot.auth_headers(private_key, "GET", hot.KALSHI_WS_SIGN_PATH, hot.API_KEY_ID)
                async with websockets.connect(
                    hot.KALSHI_WS,
                    open_timeout=5.0,
                    **hot._ws_header_kw(headers),
                ) as ws:
                    self._ws = ws
                    self._sid_by_ticker.clear()
                    self._ticker_by_command.clear()
                    self._pending_tickers.clear()
                    self._books.clear()
                    self._connected.set()
                    logger.info(
                        "CRYPTO_ROLLOVER_SHARED_KALSHI_CONNECTED %s",
                        json.dumps({
                            "registered_markets": len(self._targets),
                            "shared_websocket": True,
                            "order_entry": False,
                        }, separators=(",", ":")),
                    )
                    for ticker in list(self._targets):
                        await self._ensure_subscribed(ticker)
                    backoff = 0.25
                    async for raw in ws:
                        await self._handle(json.loads(raw))
                    raise RuntimeError("shared Kalshi WebSocket ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected.clear()
                self._ws = None
                self._sid_by_ticker.clear()
                self._ticker_by_command.clear()
                self._pending_tickers.clear()
                self._books.clear()
                logger.warning(
                    "CRYPTO_ROLLOVER_SHARED_KALSHI_RECONNECT type=%s error=%s registered=%s",
                    type(exc).__name__,
                    str(exc)[:220],
                    len(self._targets),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)


_HUB = SharedKalshiOrderbookHub()
_ORIGINAL_START_FEED = base._start_feed
_ORIGINAL_STOP_FEED = base._stop_feed


async def _start_feed_shared_kalshi(*, asset: str, pair: Any, stats: Any, active: bool):
    boundary_ms = int((pair.window_end.timestamp() - 900.0) * 1000)
    feed = base.WarmFeed(
        asset=asset,
        pair=pair,
        stats=stats,
        boundary_ms=boundary_ms,
        active=active,
    )
    feed.stream_tasks = [
        asyncio.create_task(
            hot._gemini_stream(pair.gemini_symbol, feed.raw_queue, stats),
            name=f"hot-gemini-{asset}-{int(pair.window_end.timestamp())}",
        )
    ]
    await _HUB.register(pair.kalshi_ticker, feed.raw_queue, stats)
    feed.pump_task = asyncio.create_task(
        base._pump_feed(feed),
        name=f"hot-pump-{asset}-{int(pair.window_end.timestamp())}",
    )
    logger.info(
        "CRYPTO_HOT_DOUBLE_BUFFER_FEED_START %s",
        json.dumps({
            "asset": asset,
            "gemini_symbol": pair.gemini_symbol,
            "kalshi_ticker": pair.kalshi_ticker,
            "window_end_utc": pair.window_end.isoformat(),
            "active": active,
            "start_relative_window_start_ms": int(time.time() * 1000) - boundary_ms,
            "kalshi_transport": "SHARED_MULTI_MARKET_WEBSOCKET",
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return feed


async def _stop_feed_shared_kalshi(feed) -> None:
    await _HUB.unregister(feed.pair.kalshi_ticker, feed.raw_queue, feed.stats)
    await _ORIGINAL_STOP_FEED(feed)


async def _start_next_early(*, asset: str, stats: Any, boundary_ts: float, prewarm_lead_s: float):
    del prewarm_lead_s
    await asyncio.sleep(max(0.0, boundary_ts - PREWARM_LEAD_S - time.time()))
    next_pair = predicted_pair(asset, now_ts=boundary_ts + 0.001)
    feed = await base._start_feed(asset=asset, pair=next_pair, stats=stats, active=False)
    logger.info(
        "CRYPTO_HOT_PREWARM_ARMED %s",
        json.dumps({
            "asset": asset,
            "boundary_utc_ms": int(boundary_ts * 1000),
            "prewarm_lead_s": PREWARM_LEAD_S,
            "next_window_end_utc": next_pair.window_end.isoformat(),
            "gemini_symbol": next_pair.gemini_symbol,
            "kalshi_ticker": next_pair.kalshi_ticker,
            "kalshi_transport": "SHARED_MULTI_MARKET_WEBSOCKET",
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return feed


async def _fetch_target(http: httpx.AsyncClient, asset: str, pair: Any, window_iso: str):
    try:
        response = await http.get(f"{readiness.KALSHI_REST}/markets/{pair.kalshi_ticker}")
        if response.status_code != 200:
            return asset, None
        market = response.json().get("market") or {}
        target = readiness.kalshi_target(market)
        return asset, target
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "CRYPTO_ROLLOVER_TARGET_ERROR asset=%s window=%s type=%s error=%s",
            asset,
            window_iso,
            type(exc).__name__,
            str(exc)[:160],
        )
        return asset, None


async def _kalshi_target_loop_prefetched(assets: tuple[str, ...]) -> None:
    requested = tuple(dict.fromkeys(
        str(asset).upper()
        for asset in assets
        if str(asset).upper() in basis.ASSET_SYMBOL
    )) or ("BTC",)
    prefetched: dict[tuple[str, str], Decimal] = {}
    last_window_iso: str | None = None
    last_logged_state: tuple[str, tuple[str, ...], tuple[str, ...]] | None = None
    last_prefetch_logged: tuple[str, tuple[str, ...]] | None = None

    limits = httpx.Limits(max_connections=12, max_keepalive_connections=8)
    async with httpx.AsyncClient(timeout=10.0, limits=limits) as http:
        while True:
            window_end = basis._next_window_end()
            window_iso = window_end.isoformat()

            if window_iso != last_window_iso:
                promoted = {
                    asset: (window_iso, prefetched.pop((asset, window_iso)))
                    for asset in requested
                    if (asset, window_iso) in prefetched
                }
                readiness._KALSHI_TARGETS.clear()
                readiness._KALSHI_TARGETS.update(promoted)
                if promoted:
                    logger.info(
                        "CRYPTO_ROLLOVER_TARGET_PROMOTED %s",
                        json.dumps({
                            "window_end_utc": window_iso,
                            "assets": sorted(promoted),
                            "promotion_lag_ms": max(0, int(time.time() * 1000) - int((window_end - timedelta(minutes=15)).timestamp() * 1000)),
                            "order_entry": False,
                        }, separators=(",", ":")),
                    )
                last_window_iso = window_iso
                last_logged_state = None

            missing_current = [
                asset for asset in requested
                if readiness._KALSHI_TARGETS.get(asset, (None,))[0] != window_iso
            ]
            if missing_current:
                pairs = {asset: predicted_pair(asset) for asset in missing_current}
                rows = await asyncio.gather(*(
                    _fetch_target(http, asset, pairs[asset], window_iso)
                    for asset in missing_current
                ))
                for asset, target in rows:
                    if target is not None:
                        readiness._KALSHI_TARGETS[asset] = (window_iso, target)

            available = sorted(
                asset for asset in requested
                if readiness._KALSHI_TARGETS.get(asset, (None,))[0] == window_iso
            )
            missing = sorted(set(requested) - set(available))
            state = (window_iso, tuple(available), tuple(missing))
            if state != last_logged_state:
                logger.info(
                    "CRYPTO_MICRO_READY_TARGET_CONTEXT %s",
                    json.dumps({
                        "window_end_utc": window_iso,
                        "assets_with_kalshi_target": available,
                        "missing_assets": missing,
                        "retrying_missing_targets": bool(missing),
                        "target_fetch_mode": "CONCURRENT_WITH_NEXT_WINDOW_PREFETCH",
                        "minimum_double_boundary_bps": str(readiness.MIN_DOUBLE_BOUNDARY_BPS),
                        "trade_authorized": False,
                        "read_only": True,
                        "order_entry": False,
                    }, separators=(",", ":")),
                )
                last_logged_state = state

            seconds_to_boundary = window_end.timestamp() - time.time()
            if seconds_to_boundary <= TARGET_PREFETCH_LEAD_S:
                next_end = window_end + timedelta(minutes=15)
                next_iso = next_end.isoformat()
                missing_next = [
                    asset for asset in requested
                    if (asset, next_iso) not in prefetched
                ]
                if missing_next:
                    pairs = {
                        asset: predicted_pair(asset, now_ts=window_end.timestamp() + 0.001)
                        for asset in missing_next
                    }
                    rows = await asyncio.gather(*(
                        _fetch_target(http, asset, pairs[asset], next_iso)
                        for asset in missing_next
                    ))
                    for asset, target in rows:
                        if target is not None:
                            prefetched[(asset, next_iso)] = target
                ready_next = tuple(sorted(
                    asset for asset in requested if (asset, next_iso) in prefetched
                ))
                prefetch_state = (next_iso, ready_next)
                if ready_next and prefetch_state != last_prefetch_logged:
                    logger.info(
                        "CRYPTO_ROLLOVER_TARGET_PREFETCH_READY %s",
                        json.dumps({
                            "window_end_utc": next_iso,
                            "assets": list(ready_next),
                            "seconds_before_boundary": round(max(0.0, seconds_to_boundary), 3),
                            "order_entry": False,
                        }, separators=(",", ":")),
                    )
                    last_prefetch_logged = prefetch_state

            if missing:
                sleep_s = TARGET_RETRY_S
            elif seconds_to_boundary > TARGET_PREFETCH_LEAD_S:
                sleep_s = max(0.05, min(5.0, seconds_to_boundary - TARGET_PREFETCH_LEAD_S))
            elif seconds_to_boundary > 2.0:
                sleep_s = TARGET_RETRY_S
            else:
                sleep_s = 0.05
            await asyncio.sleep(sleep_s)


_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # ``prewarmed_safe`` has already installed activation/event-order safety by the
    # time v2 imports this module.  Replace only transport/start-stop functions.
    base._start_feed = _start_feed_shared_kalshi
    base._stop_feed = _stop_feed_shared_kalshi
    base._start_next_at = _start_next_early
    readiness._kalshi_target_loop = _kalshi_target_loop_prefetched
    _INSTALLED = True
    logger.warning(
        "CRYPTO_ROLLOVER_OPTIMIZER_INSTALLED kalshi_ws=shared_multi_market prewarm_lead_s=%s target_prefetch_lead_s=%s risk_gates_changed=false",
        PREWARM_LEAD_S,
        TARGET_PREFETCH_LEAD_S,
    )

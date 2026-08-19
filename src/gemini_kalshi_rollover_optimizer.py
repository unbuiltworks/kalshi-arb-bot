"""Latency-only rollover optimizer for the bounded Gemini/Kalshi live canary.

This module changes market-data transport only. It does not change quantity, profit,
settlement, boundary, freshness, skew, depth, or execution gates.

Kalshi's orderbook channel supports multiple ``market_tickers`` on one subscription
SID. Sequence numbers belong to that multiplexed subscription stream, not to each
individual market book. The hub therefore validates one contiguous SID sequence while
routing snapshots/deltas into independent market books. Any real sequence gap tears
down the whole socket and rebuilds every book from fresh snapshots (fail closed).

The optimizer also fetches current Kalshi settlement targets concurrently and prefetches
the next window's targets before rollover. Existing activation/event-order safety still
prevents next-window quotes from entering economics before their boundary.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
import json
import logging
import time
from typing import Any, Iterable

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

PREWARM_LEAD_S = 5.0
TARGET_PREFETCH_LEAD_S = 60.0
TARGET_RETRY_S = 0.25


class SharedSequenceGap(RuntimeError):
    """A missing message on the single multiplexed orderbook subscription."""


def _tickers(values: Iterable[str]) -> list[str]:
    return sorted({str(value) for value in values if str(value)})


def _subscribe_payload(command_id: int, tickers: Iterable[str]) -> dict[str, Any]:
    markets = _tickers(tickers)
    if not markets:
        raise ValueError("shared Kalshi subscription requires at least one market")
    return {
        "id": int(command_id),
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta"],
            "market_tickers": markets,
            # Preserve the executor's already-validated legacy two-scale book math.
            "use_yes_price": False,
        },
    }


def _update_payload(
    command_id: int,
    sid: int,
    tickers: Iterable[str],
    action: str,
) -> dict[str, Any]:
    markets = _tickers(tickers)
    if not markets:
        raise ValueError("subscription update requires at least one market")
    if action not in {"add_markets", "delete_markets", "get_snapshot"}:
        raise ValueError(f"unsupported subscription action: {action}")
    return {
        "id": int(command_id),
        "cmd": "update_subscription",
        "params": {
            "sids": [int(sid)],
            "market_tickers": markets,
            "action": action,
        },
    }


def _snapshot_payload(command_id: int, sid: int, tickers: Iterable[str]) -> dict[str, Any]:
    return _update_payload(command_id, sid, tickers, "get_snapshot")


class SharedKalshiOrderbookHub:
    """One authenticated socket + one SID for all current/next Kalshi books."""

    def __init__(self) -> None:
        self._targets: dict[str, list[tuple[asyncio.Queue, Any]]] = defaultdict(list)
        self._books: dict[str, Any] = {}
        self._snapshot_ready: set[str] = set()
        self._subscribed_markets: set[str] = set()
        self._initial_markets: set[str] = set()
        self._pending_ops: dict[int, tuple[str, set[str]]] = {}
        self._ws: Any | None = None
        self._sid: int | None = None
        self._last_seq: int | None = None
        self._task: asyncio.Task[Any] | None = None
        self._send_lock = asyncio.Lock()
        self._registry_lock = asyncio.Lock()
        self._next_command_id = 1000
        self._connected = asyncio.Event()
        self.connection_count = 0
        self.sequence_gaps = 0

    @property
    def sid(self) -> int | None:
        return self._sid

    @property
    def last_seq(self) -> int | None:
        return self._last_seq

    def health(self) -> dict[str, Any]:
        return {
            "connected": self._connected.is_set(),
            "sid": self._sid,
            "last_seq": self._last_seq,
            "connection_count": self.connection_count,
            "sequence_gaps": self.sequence_gaps,
            "desired_markets": sorted(self._targets),
            "subscribed_markets": sorted(self._subscribed_markets),
            "snapshot_ready_markets": sorted(self._snapshot_ready),
        }

    def _command_id(self) -> int:
        self._next_command_id += 1
        return self._next_command_id

    def _bump_gap_stats(self) -> None:
        seen: set[int] = set()
        for targets in self._targets.values():
            for _queue, stats in targets:
                marker = id(stats)
                if marker in seen:
                    continue
                seen.add(marker)
                stats.kalshi_sequence_gaps += 1

    def _observe_sequence(self, payload: dict[str, Any]) -> bool:
        """Validate every sequenced message for our SID, including command OKs."""
        if self._sid is None:
            return True
        sid_raw = payload.get("sid")
        seq_raw = payload.get("seq")
        if sid_raw is None or seq_raw is None:
            return True
        sid = int(sid_raw)
        if sid != self._sid:
            # No other subscription SID should exist on this dedicated connection.
            raise RuntimeError(f"unexpected Kalshi sid expected={self._sid} got={sid}")
        seq = int(seq_raw)
        if self._last_seq is None:
            self._last_seq = seq
            return True
        if seq <= self._last_seq:
            # Duplicate/replayed message: never apply it twice.
            return False
        expected = self._last_seq + 1
        if seq != expected:
            self.sequence_gaps += 1
            self._bump_gap_stats()
            raise SharedSequenceGap(
                f"shared Kalshi sequence gap sid={self._sid} "
                f"previous={self._last_seq} current={seq} expected={expected}"
            )
        self._last_seq = seq
        return True

    async def register(self, ticker: str, queue: asyncio.Queue, stats: Any) -> None:
        ticker = str(ticker)
        async with self._registry_lock:
            target = (queue, stats)
            if target not in self._targets[ticker]:
                self._targets[ticker].append(target)
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(
                    self._run(), name="kalshi-shared-orderbook-hub"
                )
        if self._connected.is_set() and self._sid is not None:
            await self._sync_markets()

    async def unregister(self, ticker: str, queue: asyncio.Queue, stats: Any) -> None:
        ticker = str(ticker)
        async with self._registry_lock:
            targets = self._targets.get(ticker, [])
            target = (queue, stats)
            if target in targets:
                targets.remove(target)
            if not targets:
                self._targets.pop(ticker, None)
                self._books.pop(ticker, None)
                self._snapshot_ready.discard(ticker)
        if self._connected.is_set() and self._sid is not None:
            await self._sync_markets()

    async def _send(self, payload: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("shared Kalshi websocket is not connected")
        async with self._send_lock:
            await ws.send(json.dumps(payload, separators=(",", ":")))

    def _pending(self, action: str) -> set[str]:
        out: set[str] = set()
        for pending_action, markets in self._pending_ops.values():
            if pending_action == action:
                out.update(markets)
        return out

    async def _send_update(self, action: str, markets: set[str]) -> None:
        if not markets or self._sid is None:
            return
        cid = self._command_id()
        self._pending_ops[cid] = (action, set(markets))
        try:
            await self._send(_update_payload(cid, self._sid, markets, action))
        except Exception:
            self._pending_ops.pop(cid, None)
            raise

    async def _sync_markets(self) -> None:
        if self._sid is None or self._ws is None:
            return
        desired = set(self._targets)
        pending_add = self._pending("add_markets")
        pending_delete = self._pending("delete_markets")
        to_add = desired - self._subscribed_markets - pending_add
        to_delete = self._subscribed_markets - desired - pending_delete
        if to_add:
            await self._send_update("add_markets", to_add)
        if to_delete:
            await self._send_update("delete_markets", to_delete)

    async def _handle(self, payload: dict[str, Any]) -> None:
        typ = str(payload.get("type") or "")
        msg = payload.get("msg") or {}

        if typ == "subscribed":
            sid_raw = msg.get("sid", payload.get("sid"))
            if sid_raw is None:
                raise RuntimeError("Kalshi subscribed response missing sid")
            self._sid = int(sid_raw)
            self._subscribed_markets = set(self._initial_markets)
            logger.info(
                "CRYPTO_ROLLOVER_SHARED_KALSHI_SUBSCRIBED %s",
                json.dumps({
                    "sid": self._sid,
                    "markets": sorted(self._subscribed_markets),
                    "single_subscription_sid": True,
                    "shared_websocket": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )
            await self._sync_markets()
            return

        if not self._observe_sequence(payload):
            return

        if typ == "error":
            cid_raw = payload.get("id")
            try:
                cid = int(cid_raw)
            except (TypeError, ValueError):
                cid = -1
            pending = self._pending_ops.pop(cid, None)
            raise RuntimeError(
                f"Kalshi shared subscription command failed pending={pending} msg={str(msg)[:220]}"
            )

        if typ == "ok":
            cid_raw = payload.get("id")
            try:
                cid = int(cid_raw)
            except (TypeError, ValueError):
                cid = -1
            pending = self._pending_ops.pop(cid, None)
            if pending is not None:
                action, markets = pending
                if action == "add_markets":
                    self._subscribed_markets.update(markets)
                elif action == "delete_markets":
                    self._subscribed_markets.difference_update(markets)
                    for ticker in markets:
                        if ticker not in self._targets:
                            self._books.pop(ticker, None)
                            self._snapshot_ready.discard(ticker)
                logger.info(
                    "CRYPTO_ROLLOVER_SHARED_KALSHI_UPDATED %s",
                    json.dumps({
                        "sid": self._sid,
                        "action": action,
                        "markets": sorted(markets),
                        "subscribed_markets": sorted(self._subscribed_markets),
                        "seq": self._last_seq,
                        "order_entry": False,
                    }, separators=(",", ":")),
                )
            await self._sync_markets()
            return

        if typ not in {"orderbook_snapshot", "orderbook_delta"}:
            return

        ticker = str(msg.get("market_ticker") or "")
        if not ticker:
            raise RuntimeError("Kalshi orderbook message missing market_ticker")
        targets = list(self._targets.get(ticker, ()))
        if not targets:
            return

        if typ == "orderbook_snapshot":
            book = hot.KalshiOrderBook(ticker)
            if not book.apply(payload):
                raise RuntimeError(f"failed to apply Kalshi snapshot ticker={ticker}")
            self._books[ticker] = book
            self._snapshot_ready.add(ticker)
        else:
            if ticker not in self._snapshot_ready:
                raise RuntimeError(
                    f"Kalshi delta arrived before snapshot ticker={ticker} sid={self._sid}"
                )
            book = self._books[ticker]
            if not book.apply(payload):
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
                headers = hot.auth_headers(
                    private_key, "GET", hot.KALSHI_WS_SIGN_PATH, hot.API_KEY_ID
                )
                async with websockets.connect(
                    hot.KALSHI_WS,
                    open_timeout=5.0,
                    **hot._ws_header_kw(headers),
                ) as ws:
                    self._ws = ws
                    self._sid = None
                    self._last_seq = None
                    self._pending_ops.clear()
                    self._books.clear()
                    self._snapshot_ready.clear()
                    self._subscribed_markets.clear()
                    self._initial_markets = set(self._targets)
                    if not self._initial_markets:
                        raise RuntimeError("shared Kalshi hub connected with no markets")
                    self.connection_count += 1
                    self._connected.set()
                    cid = self._command_id()
                    await self._send(_subscribe_payload(cid, self._initial_markets))
                    logger.info(
                        "CRYPTO_ROLLOVER_SHARED_KALSHI_CONNECTED %s",
                        json.dumps({
                            "connection_count": self.connection_count,
                            "initial_markets": sorted(self._initial_markets),
                            "single_subscription_sid": True,
                            "shared_websocket": True,
                            "order_entry": False,
                        }, separators=(",", ":")),
                    )
                    backoff = 0.25
                    async for raw in ws:
                        await self._handle(json.loads(raw))
                    raise RuntimeError("shared Kalshi WebSocket ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected.clear()
                self._ws = None
                self._sid = None
                self._last_seq = None
                self._pending_ops.clear()
                self._books.clear()
                self._snapshot_ready.clear()
                self._subscribed_markets.clear()
                logger.warning(
                    "CRYPTO_ROLLOVER_SHARED_KALSHI_RECONNECT type=%s error=%s registered=%s gaps=%s",
                    type(exc).__name__,
                    str(exc)[:260],
                    len(self._targets),
                    self.sequence_gaps,
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
            "kalshi_transport": "ONE_WS_ONE_SID_MULTI_MARKET",
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
            "kalshi_transport": "ONE_WS_ONE_SID_MULTI_MARKET",
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
                            "promotion_lag_ms": max(
                                0,
                                int(time.time() * 1000)
                                - int((window_end - timedelta(minutes=15)).timestamp() * 1000),
                            ),
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
                        asset: predicted_pair(
                            asset, now_ts=window_end.timestamp() + 0.001
                        )
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
                            "seconds_before_boundary": round(
                                max(0.0, seconds_to_boundary), 3
                            ),
                            "order_entry": False,
                        }, separators=(",", ":")),
                    )
                    last_prefetch_logged = prefetch_state

            if missing:
                sleep_s = TARGET_RETRY_S
            elif seconds_to_boundary > TARGET_PREFETCH_LEAD_S:
                sleep_s = max(
                    0.05,
                    min(5.0, seconds_to_boundary - TARGET_PREFETCH_LEAD_S),
                )
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
    # ``prewarmed_safe`` already owns activation/event-order safety. Replace only
    # market-data transport and target-fetch timing.
    base._start_feed = _start_feed_shared_kalshi
    base._stop_feed = _stop_feed_shared_kalshi
    base._start_next_at = _start_next_early
    readiness._kalshi_target_loop = _kalshi_target_loop_prefetched
    _INSTALLED = True
    logger.warning(
        "CRYPTO_ROLLOVER_OPTIMIZER_INSTALLED kalshi_ws=one_ws_one_sid_multi_market prewarm_lead_s=%s target_prefetch_lead_s=%s risk_gates_changed=false",
        PREWARM_LEAD_S,
        TARGET_PREFETCH_LEAD_S,
    )

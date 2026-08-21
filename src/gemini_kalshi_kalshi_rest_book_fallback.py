"""Fail-closed market-data fallback for the live Gemini/Kalshi 15m scanner.

Kalshi uses the public exact REST orderbook when authenticated Kalshi WebSocket
handshakes are rate-limited. Gemini prediction-market bookTicker streams are
multiplexed onto one public WebSocket connection so BTC/ETH/SOL/XRP do not create a
four-connection reconnect storm when the venue throttles handshakes.

The Kalshi quote timestamp is conservatively stamped at request start (not response
receipt), so network latency makes freshness/skew checks harder rather than easier.
Existing q=1, profit, boundary, 50ms survival, <=50ms quote-skew and exact-payload
gates remain in control of execution.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx
import websockets

from src import gemini_kalshi_predictive_prewarmed as base
from src import gemini_kalshi_rollover_optimizer as rollover
from src import gemini_kalshi_micro_live_readiness_20bp as readiness

hot = base.hot
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_kalshi_rest_book_fallback")
POLL_SLEEP_S = 0.08
MAX_BACKOFF_S = 2.0
DEPTH = 10

# Preserve the pre-patch lifecycle function. install() replaces base._stop_feed with
# our wrapper; calling base._stop_feed from inside that wrapper would recurse forever
# during rollover/cleanup and crash the live campaign.
_ORIGINAL_STOP_FEED = base._stop_feed

# Gemini supports multiple public stream names on one SUBSCRIBE request. Keep one
# process-wide connection and fan each symbol update into the feed(s) that requested
# it. This reduces connection handshakes without changing any execution/economics gate.
_GEMINI_SUBSCRIBERS: dict[str, dict[int, tuple[asyncio.Queue, Any]]] = {}
_GEMINI_MANAGER_TASK: asyncio.Task | None = None
_GEMINI_CHANGE_EVENT: asyncio.Event | None = None


def _gemini_change_event() -> asyncio.Event:
    global _GEMINI_CHANGE_EVENT
    if _GEMINI_CHANGE_EVENT is None:
        _GEMINI_CHANGE_EVENT = asyncio.Event()
    return _GEMINI_CHANGE_EVENT


def _gemini_stream_names(symbols: set[str]) -> list[str]:
    return [f"{symbol}@bookTicker" for symbol in sorted(symbols)]


def _gemini_snapshot() -> set[str]:
    return {symbol for symbol, subscribers in _GEMINI_SUBSCRIBERS.items() if subscribers}


def _gemini_increment_reconnects() -> None:
    seen: set[int] = set()
    for subscribers in _GEMINI_SUBSCRIBERS.values():
        for _queue, stats in subscribers.values():
            ident = id(stats)
            if ident not in seen:
                stats.gemini_reconnects += 1
                seen.add(ident)


async def _gemini_shared_manager() -> None:
    backoff = 0.25
    first = True
    change = _gemini_change_event()
    while True:
        symbols = _gemini_snapshot()
        if not symbols:
            change.clear()
            await change.wait()
            continue

        try:
            async with websockets.connect(hot.GEMINI_WS, open_timeout=5.0) as ws:
                await ws.send(json.dumps({
                    "id": "hot-shared",
                    "method": "SUBSCRIBE",
                    "params": _gemini_stream_names(symbols),
                }))
                subscribed = set(symbols)
                backoff = 0.25
                logger.info(
                    "CRYPTO_GEMINI_SHARED_CONNECTED %s",
                    json.dumps({
                        "symbols": sorted(subscribed),
                        "stream_count": len(subscribed),
                        "connections": 1,
                        "risk_gates_changed": False,
                    }, separators=(",", ":")),
                )

                while True:
                    recv_task = asyncio.create_task(ws.recv())
                    change_task = asyncio.create_task(change.wait())
                    done, pending = await asyncio.wait(
                        {recv_task, change_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)

                    if change_task in done:
                        change.clear()
                        desired = _gemini_snapshot()
                        additions = desired - subscribed
                        removals = subscribed - desired
                        if additions:
                            await ws.send(json.dumps({
                                "id": "hot-shared-add",
                                "method": "SUBSCRIBE",
                                "params": _gemini_stream_names(additions),
                            }))
                        if removals:
                            await ws.send(json.dumps({
                                "id": "hot-shared-remove",
                                "method": "UNSUBSCRIBE",
                                "params": _gemini_stream_names(removals),
                            }))
                        subscribed = desired
                        if not subscribed:
                            break
                        continue

                    raw = recv_task.result()
                    receive_ms = int(time.time() * 1000)
                    mono_ns = time.perf_counter_ns()
                    payload = json.loads(raw)
                    if payload.get("status") is not None:
                        if int(payload.get("status")) >= 400:
                            raise RuntimeError(f"Gemini WS subscription error: {payload}")
                        continue
                    symbol = str(payload.get("s") or "")
                    subscribers = list(_GEMINI_SUBSCRIBERS.get(symbol, {}).values())
                    if not subscribers:
                        continue
                    try:
                        top = hot.parse_gemini_book_ticker(payload, received_ms=receive_ms)
                    except (KeyError, ValueError, TypeError):
                        continue
                    for queue, stats in subscribers:
                        stats.events += 1
                        stats.gemini_events += 1
                        hot._enqueue(
                            queue,
                            ("gemini", receive_ms, None, top, mono_ns),
                            stats,
                        )

            first = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not first:
                _gemini_increment_reconnects()
            first = False
            logger.warning(
                "CRYPTO_GEMINI_SHARED_RECONNECT %s",
                json.dumps({
                    "symbols": sorted(_gemini_snapshot()),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                    "backoff_s": backoff,
                    "connections": 1,
                }, separators=(",", ":")),
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 5.0)


async def _gemini_shared_stream(symbol: str, queue: asyncio.Queue, stats: Any) -> None:
    global _GEMINI_MANAGER_TASK
    token = id(asyncio.current_task())
    _GEMINI_SUBSCRIBERS.setdefault(symbol, {})[token] = (queue, stats)
    change = _gemini_change_event()
    change.set()
    if _GEMINI_MANAGER_TASK is None or _GEMINI_MANAGER_TASK.done():
        _GEMINI_MANAGER_TASK = asyncio.create_task(
            _gemini_shared_manager(),
            name="hot-gemini-shared-manager",
        )
    try:
        await asyncio.Future()
    finally:
        subscribers = _GEMINI_SUBSCRIBERS.get(symbol)
        if subscribers is not None:
            subscribers.pop(token, None)
            if not subscribers:
                _GEMINI_SUBSCRIBERS.pop(symbol, None)
        change.set()


def _levels(rows: Any) -> dict[Decimal, Decimal]:
    out: dict[Decimal, Decimal] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        try:
            price = D(str(row[0]))
            qty = D(str(row[1]))
        except Exception:
            continue
        if D("0") <= price <= D("1") and qty > 0:
            out[price] = qty
    return out


def _top_from_rest(ticker: str, payload: dict[str, Any], quote_ts_ms: int):
    book_payload = payload.get("orderbook_fp") or {}
    yes = _levels(book_payload.get("yes_dollars"))
    no = _levels(book_payload.get("no_dollars"))
    if not yes and not no:
        return None
    book = hot.KalshiOrderBook(ticker)
    book.yes = yes
    book.no = no
    # REST doesn't expose a reliable exchange event timestamp. Keep it None and use
    # the conservative request-start timestamp on TopOfBook for all freshness/skew.
    return book.top(quote_ts_ms)


async def _rest_book_stream(ticker: str, queue: asyncio.Queue, stats: Any) -> None:
    backoff = POLL_SLEEP_S
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
    async with httpx.AsyncClient(timeout=2.0, limits=limits) as http:
        while True:
            request_start_ms = int(time.time() * 1000)
            mono_ns = time.perf_counter_ns()
            try:
                response = await http.get(
                    f"{readiness.KALSHI_REST}/markets/{ticker}/orderbook",
                    params={"depth": DEPTH},
                )
                if response.status_code == 429:
                    stats.kalshi_reconnects += 1
                    logger.warning(
                        "CRYPTO_KALSHI_REST_BOOK_429 asset=%s ticker=%s backoff_s=%.3f",
                        stats.asset, ticker, backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(MAX_BACKOFF_S, max(POLL_SLEEP_S, backoff * 2.0))
                    continue
                if response.status_code != 200:
                    stats.kalshi_reconnects += 1
                    await asyncio.sleep(min(MAX_BACKOFF_S, max(0.25, backoff)))
                    continue
                payload = response.json()
                top = _top_from_rest(ticker, payload, request_start_ms)
                if top is not None:
                    receive_ms = int(time.time() * 1000)
                    stats.events += 1
                    stats.kalshi_events += 1
                    hot._enqueue(
                        queue,
                        ("kalshi", receive_ms, None, top, mono_ns),
                        stats,
                    )
                backoff = POLL_SLEEP_S
                await asyncio.sleep(POLL_SLEEP_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                stats.kalshi_reconnects += 1
                logger.warning(
                    "CRYPTO_KALSHI_REST_BOOK_ERROR asset=%s ticker=%s type=%s error=%s backoff_s=%.3f",
                    stats.asset, ticker, type(exc).__name__, str(exc)[:180], backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(MAX_BACKOFF_S, max(POLL_SLEEP_S, backoff * 2.0))


async def _start_feed_rest_kalshi(*, asset: str, pair: Any, stats: Any, active: bool):
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
            _gemini_shared_stream(pair.gemini_symbol, feed.raw_queue, stats),
            name=f"hot-gemini-shared-{asset}-{int(pair.window_end.timestamp())}",
        ),
        asyncio.create_task(
            _rest_book_stream(pair.kalshi_ticker, feed.raw_queue, stats),
            name=f"hot-kalshi-rest-{asset}-{int(pair.window_end.timestamp())}",
        ),
    ]
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
            "gemini_transport": "ONE_SHARED_WS_MULTI_STREAM",
            "kalshi_transport": "REST_EXACT_ORDERBOOK_FAIL_CLOSED",
            "kalshi_quote_timestamp": "REQUEST_START_CONSERVATIVE",
            "risk_gates_changed": False,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return feed


async def _stop_feed_rest_kalshi(feed) -> None:
    await _ORIGINAL_STOP_FEED(feed)


def install() -> None:
    # Install after rollover optimizer so these functions replace only market-data
    # transport. Predictive rollover/target logic and every execution gate remain.
    base._start_feed = _start_feed_rest_kalshi
    base._stop_feed = _stop_feed_rest_kalshi
    logger.warning(
        "CRYPTO_MARKET_DATA_FALLBACK_INSTALLED kalshi_endpoint=exact_orderbook depth=10 "
        "kalshi_quote_timestamp=request_start kalshi_poll_sleep_s=0.08 "
        "gemini_transport=one_shared_ws_multi_stream risk_gates_changed=false"
    )

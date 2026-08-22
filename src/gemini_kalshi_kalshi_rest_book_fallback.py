"""Fail-closed market-data fallback for the live Gemini/Kalshi 15m scanner.

Kalshi uses the public exact REST orderbook when authenticated Kalshi WebSocket
handshakes are rate-limited. Gemini prediction books and the Gemini public-spot basis
proxy now share ONE persistent authenticated WebSocket: prediction books use the
read-only ``depth`` request method while BTC/ETH/SOL/XRP spot mids arrive through
bookTicker subscriptions on that same connection. This removes an entire Gemini
handshake from the live child and gives market data first claim on the connection
budget before the private execution socket prewarms.

Both venue-book snapshot timestamps are conservatively stamped at request start, so
network latency makes freshness/skew checks harder rather than easier. Existing q=1,
profit, double-boundary, 50ms survival, <=50ms quote-skew and exact-payload gates remain
in control of execution. This module exposes no order method.
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
from src.gemini_prediction_auth import authenticated_ws_connect, has_credentials
from src.gemini_ws_connection_coordination import (
    mark_market_data_ready,
    mark_market_data_unready,
)

hot = base.hot
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_kalshi_rest_book_fallback")

# Four independent 80ms Kalshi loops repeatedly hit HTTP 429. The paced/staggered
# cadence preserves frequent usable snapshots while staying below the observed wall.
POLL_SLEEP_S = 0.18
MAX_BACKOFF_S = 2.0
DEPTH = 10
GEMINI_DEPTH = 5
GEMINI_REQUEST_SPACING_S = 0.05
_KALSHI_INITIAL_STAGGER_S = {
    "BTC": 0.00,
    "ETH": 0.05,
    "SOL": 0.10,
    "XRP": 0.15,
}
_SPOT_STREAMS = [
    f"{symbol}@bookTicker" for symbol in readiness.basis.ASSET_SYMBOL.values()
]

# Preserve the pre-patch lifecycle function. install() replaces base._stop_feed with
# our wrapper; calling base._stop_feed from inside that wrapper would recurse forever.
_ORIGINAL_STOP_FEED = base._stop_feed

_GEMINI_SUBSCRIBERS: dict[str, dict[int, tuple[asyncio.Queue, Any]]] = {}
_GEMINI_MANAGER_TASK: asyncio.Task | None = None
_GEMINI_CHANGE_EVENT: asyncio.Event | None = None


def _gemini_change_event() -> asyncio.Event:
    global _GEMINI_CHANGE_EVENT
    if _GEMINI_CHANGE_EVENT is None:
        _GEMINI_CHANGE_EVENT = asyncio.Event()
    return _GEMINI_CHANGE_EVENT


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


def _best_level(rows: Any, *, highest: bool) -> tuple[D, D] | None:
    levels: list[tuple[D, D]] = []
    if not isinstance(rows, list):
        return None
    for row in rows:
        try:
            if isinstance(row, dict):
                price = D(str(row.get("price")))
                qty = D(str(row.get("amount") or row.get("quantity") or row.get("qty")))
            else:
                price = D(str(row[0]))
                qty = D(str(row[1]))
        except Exception:
            continue
        if D("0") <= price <= D("1") and qty > 0:
            levels.append((price, qty))
    if not levels:
        return None
    return (max if highest else min)(levels, key=lambda level: level[0])


def _gemini_top_from_depth(symbol: str, payload: dict[str, Any], quote_ts_ms: int):
    candidates = [payload.get("result"), payload.get("data"), payload]
    data = next(
        (candidate for candidate in candidates if isinstance(candidate, dict) and "bids" in candidate and "asks" in candidate),
        None,
    )
    if data is None:
        return None
    bid = _best_level(data.get("bids"), highest=True)
    ask = _best_level(data.get("asks"), highest=False)
    if bid is None or ask is None:
        return None
    return hot.parse_gemini_book_ticker(
        {
            "s": symbol,
            "b": str(bid[0]),
            "B": str(bid[1]),
            "a": str(ask[0]),
            "A": str(ask[1]),
        },
        received_ms=quote_ts_ms,
    )


def _handle_shared_spot(payload: dict[str, Any], receive_ms: int) -> bool:
    """Route Gemini spot bookTicker frames into the existing basis state."""
    symbol = str(payload.get("s") or "").lower()
    asset = readiness.basis.SYMBOL_ASSET.get(symbol)
    if asset is None:
        return False
    try:
        bid = D(str(payload.get("b")))
        ask = D(str(payload.get("a")))
    except Exception:
        return True
    if bid <= 0 or ask <= 0 or ask < bid:
        return True
    readiness.basis._STATES[asset].update(
        bid=bid,
        ask=ask,
        receive_ms=int(receive_ms),
    )
    return True


async def _recv_for_id(ws, request_id: str, *, timeout: float) -> dict[str, Any]:
    """Read until one RPC response arrives, consuming spot stream frames en route."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        receive_ms = int(time.time() * 1000)
        payload = json.loads(raw)
        if _handle_shared_spot(payload, receive_ms):
            continue
        message_id = str(payload.get("id") or "")
        if message_id == request_id:
            return payload
        # Ignore forward-compatible unsolicited stream/event frames. A response carrying
        # a different request id would violate our single-request-at-a-time discipline.
        if message_id:
            raise RuntimeError(
                f"Gemini shared WS response id mismatch expected={request_id} got={message_id}"
            )


async def _gemini_connect_market_data():
    if has_credentials():
        return await authenticated_ws_connect(timeout=5.0), "AUTHENTICATED_SHARED_WS_DEPTH_RPC_PLUS_SPOT"
    return await websockets.connect(hot.GEMINI_WS, open_timeout=5.0), "PUBLIC_SHARED_WS_DEPTH_RPC_PLUS_SPOT"


async def _subscribe_shared_spot(ws) -> None:
    request_id = "shared-basis-spot-subscribe"
    await ws.send(json.dumps({
        "id": request_id,
        "method": "SUBSCRIBE",
        "params": _SPOT_STREAMS,
    }, separators=(",", ":")))
    ack = await _recv_for_id(ws, request_id, timeout=5.0)
    if int(ack.get("status", 0)) != 200:
        raise RuntimeError(f"Gemini shared spot subscription failed status={ack.get('status')}")


async def _gemini_shared_manager() -> None:
    backoff = 1.0
    request_seq = 0
    schema_warned = False
    while True:
        symbols = sorted(_gemini_snapshot())
        if not symbols:
            mark_market_data_unready()
            change = _gemini_change_event()
            change.clear()
            await change.wait()
            continue

        ws = None
        transport = "UNKNOWN"
        try:
            mark_market_data_unready()
            ws, transport = await _gemini_connect_market_data()
            await _subscribe_shared_spot(ws)
            mark_market_data_ready()
            backoff = 1.0
            logger.info(
                "CRYPTO_GEMINI_SHARED_CONNECTED %s",
                json.dumps({
                    "symbols": symbols,
                    "spot_streams": _SPOT_STREAMS,
                    "connections": 1,
                    "transport": transport,
                    "depth": GEMINI_DEPTH,
                    "request_spacing_s": GEMINI_REQUEST_SPACING_S,
                    "basis_spot_on_same_socket": True,
                    "market_data_only": True,
                    "risk_gates_changed": False,
                }, separators=(",", ":")),
            )

            while _gemini_snapshot():
                for symbol in sorted(_gemini_snapshot()):
                    subscribers = list(_GEMINI_SUBSCRIBERS.get(symbol, {}).values())
                    if not subscribers:
                        continue
                    request_seq += 1
                    request_id = f"depth-{request_seq}-{symbol}"
                    request_start_ms = int(time.time() * 1000)
                    mono_ns = time.perf_counter_ns()
                    await ws.send(json.dumps({
                        "id": request_id,
                        "method": "depth",
                        "params": {"symbol": symbol, "limit": GEMINI_DEPTH},
                    }, separators=(",", ":")))
                    payload = await _recv_for_id(ws, request_id, timeout=2.0)
                    status = int(payload.get("status", 200))
                    if status >= 400:
                        raise RuntimeError(f"Gemini depth RPC status={status}")
                    top = _gemini_top_from_depth(symbol, payload, request_start_ms)
                    if top is None:
                        if not schema_warned:
                            nested = payload.get("result") or payload.get("data") or {}
                            logger.warning(
                                "CRYPTO_GEMINI_DEPTH_SCHEMA_UNSUPPORTED top_keys=%s nested_keys=%s",
                                sorted(str(key) for key in payload.keys()),
                                sorted(str(key) for key in nested.keys()) if isinstance(nested, dict) else [],
                            )
                            schema_warned = True
                        raise RuntimeError("Gemini depth RPC returned no explicit two-sided book")
                    receive_ms = int(time.time() * 1000)
                    for queue, stats in subscribers:
                        stats.events += 1
                        stats.gemini_events += 1
                        hot._enqueue(
                            queue,
                            ("gemini", receive_ms, None, top, mono_ns),
                            stats,
                        )
                    await asyncio.sleep(GEMINI_REQUEST_SPACING_S)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _gemini_increment_reconnects()
            logger.warning(
                "CRYPTO_GEMINI_SHARED_RECONNECT %s",
                json.dumps({
                    "symbols": sorted(_gemini_snapshot()),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                    "backoff_s": backoff,
                    "connections": 1,
                    "transport": transport,
                }, separators=(",", ":")),
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)
        finally:
            mark_market_data_unready()
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass


async def _gemini_shared_stream(symbol: str, queue: asyncio.Queue, stats: Any) -> None:
    global _GEMINI_MANAGER_TASK
    token = id(asyncio.current_task())
    _GEMINI_SUBSCRIBERS.setdefault(symbol, {})[token] = (queue, stats)
    _gemini_change_event().set()
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
        _gemini_change_event().set()


async def _basis_spot_on_shared_socket() -> None:
    """Placeholder replacing the old extra basis WS; shared manager updates _STATES."""
    await asyncio.Future()


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
    return book.top(quote_ts_ms)


async def _rest_book_stream(ticker: str, queue: asyncio.Queue, stats: Any) -> None:
    backoff = POLL_SLEEP_S
    initial_stagger = _KALSHI_INITIAL_STAGGER_S.get(str(stats.asset).upper(), 0.0)
    if initial_stagger > 0:
        await asyncio.sleep(initial_stagger)
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
                    retry_after = response.headers.get("Retry-After")
                    try:
                        retry_s = float(retry_after) if retry_after else backoff
                    except (TypeError, ValueError):
                        retry_s = backoff
                    logger.warning(
                        "CRYPTO_KALSHI_REST_BOOK_429 asset=%s ticker=%s backoff_s=%.3f",
                        stats.asset, ticker, retry_s,
                    )
                    await asyncio.sleep(max(POLL_SLEEP_S, retry_s))
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
    feed = base.WarmFeed(asset=asset, pair=pair, stats=stats, boundary_ms=boundary_ms, active=active)
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
    feed.pump_task = asyncio.create_task(base._pump_feed(feed), name=f"hot-pump-{asset}-{int(pair.window_end.timestamp())}")
    logger.info(
        "CRYPTO_HOT_DOUBLE_BUFFER_FEED_START %s",
        json.dumps({
            "asset": asset,
            "gemini_symbol": pair.gemini_symbol,
            "kalshi_ticker": pair.kalshi_ticker,
            "window_end_utc": pair.window_end.isoformat(),
            "active": active,
            "gemini_transport": "ONE_SHARED_WS_DEPTH_RPC_PLUS_BASIS_SPOT",
            "kalshi_transport": "REST_EXACT_ORDERBOOK_FAIL_CLOSED_PACED",
            "kalshi_poll_sleep_s": POLL_SLEEP_S,
            "kalshi_initial_stagger_s": _KALSHI_INITIAL_STAGGER_S.get(asset, 0.0),
            "quote_timestamps": "REQUEST_START_CONSERVATIVE",
            "risk_gates_changed": False,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return feed


async def _stop_feed_rest_kalshi(feed) -> None:
    await _ORIGINAL_STOP_FEED(feed)


def install() -> None:
    base._start_feed = _start_feed_rest_kalshi
    base._stop_feed = _stop_feed_rest_kalshi
    # The existing 20bp boundary gate still consumes Gemini spot, but those spot ticks
    # now arrive on the shared prediction-market socket instead of a third WS handshake.
    readiness.basis._spot_stream = _basis_spot_on_shared_socket
    logger.warning(
        "CRYPTO_MARKET_DATA_FALLBACK_INSTALLED kalshi_endpoint=exact_orderbook depth=10 "
        "kalshi_poll_sleep_s=0.18 staggered=true "
        "gemini_transport=one_shared_ws_depth_rpc_plus_basis_spot "
        "private_ws_priority=after_market_data quote_timestamp=request_start "
        "risk_gates_changed=false"
    )

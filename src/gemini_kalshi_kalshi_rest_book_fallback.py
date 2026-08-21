"""Fail-closed Kalshi REST order-book fallback for the live 15m scanner.

This is used only when authenticated Kalshi WebSocket handshakes are rate-limited.
It consumes the public exact orderbook endpoint; it never fabricates price or depth.
The quote timestamp is conservatively stamped at request start (not response receipt),
so network latency makes freshness/skew checks harder rather than easier. Existing q=1,
profit, boundary, 50ms survival, <=50ms quote-skew and exact-payload gates remain in
control of execution.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import gemini_kalshi_predictive_prewarmed as base
from src import gemini_kalshi_rollover_optimizer as rollover
from src import gemini_kalshi_micro_live_readiness_20bp as readiness

hot = base.hot
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_kalshi_rest_book_fallback")
POLL_SLEEP_S = 0.08
MAX_BACKOFF_S = 2.0
DEPTH = 10


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
            hot._gemini_stream(pair.gemini_symbol, feed.raw_queue, stats),
            name=f"hot-gemini-{asset}-{int(pair.window_end.timestamp())}",
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
            "kalshi_transport": "REST_EXACT_ORDERBOOK_FAIL_CLOSED",
            "kalshi_quote_timestamp": "REQUEST_START_CONSERVATIVE",
            "risk_gates_changed": False,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return feed


async def _stop_feed_rest_kalshi(feed) -> None:
    await base._stop_feed(feed)


def install() -> None:
    # Install after rollover optimizer so these functions replace only its market-data
    # transport. Its predictive rollover/target logic and every execution gate remain.
    base._start_feed = _start_feed_rest_kalshi
    base._stop_feed = _stop_feed_rest_kalshi
    logger.warning(
        "CRYPTO_KALSHI_REST_BOOK_FALLBACK_INSTALLED endpoint=exact_orderbook depth=10 "
        "quote_timestamp=request_start poll_sleep_s=0.08 risk_gates_changed=false"
    )

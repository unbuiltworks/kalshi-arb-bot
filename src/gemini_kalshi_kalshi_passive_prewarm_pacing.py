"""Reduce Kalshi REST load during predictive double-buffer overlap.

The active 15-minute feed keeps the proven 180ms exact-book cadence. A next-window
feed is read-only and cannot be evaluated before ``feed.active`` flips at the exact
boundary, so while passive it polls conservatively at 850ms. The sleep is sliced into
50ms checks so promotion to active accelerates immediately instead of waiting out the
passive interval.

This changes transport load only. Request-start timestamps, exact-book parsing,
freshness/skew/profit/boundary/survival gates and order authorization are unchanged.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from src import gemini_kalshi_kalshi_rest_book_fallback as rest

PASSIVE_POLL_SLEEP_S = 0.85
ACTIVATION_CHECK_S = 0.05
_INSTALLED = False


async def _sleep_for_feed(feed: Any, delay_s: float) -> None:
    """Sleep conservatively, but notice passive->active promotion within 50ms."""
    deadline = time.monotonic() + max(0.0, delay_s)
    was_active = bool(feed.active)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(ACTIVATION_CHECK_S, remaining))
        if not was_active and bool(feed.active):
            return


async def _paced_rest_book_stream(feed: Any) -> None:
    ticker = str(feed.pair.kalshi_ticker)
    stats = feed.stats
    backoff = rest.POLL_SLEEP_S
    initial_stagger = rest._KALSHI_INITIAL_STAGGER_S.get(str(stats.asset).upper(), 0.0)
    if initial_stagger > 0:
        await asyncio.sleep(initial_stagger)

    limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
    async with httpx.AsyncClient(timeout=2.0, limits=limits) as http:
        while True:
            request_start_ms = int(time.time() * 1000)
            mono_ns = time.perf_counter_ns()
            try:
                response = await http.get(
                    f"{rest.readiness.KALSHI_REST}/markets/{ticker}/orderbook",
                    params={"depth": rest.DEPTH},
                )
                if response.status_code == 429:
                    stats.kalshi_reconnects += 1
                    retry_after = response.headers.get("Retry-After")
                    try:
                        retry_s = float(retry_after) if retry_after else backoff
                    except (TypeError, ValueError):
                        retry_s = backoff
                    rest.logger.warning(
                        "CRYPTO_KALSHI_REST_BOOK_429 asset=%s ticker=%s active=%s backoff_s=%.3f",
                        stats.asset, ticker, bool(feed.active), retry_s,
                    )
                    await _sleep_for_feed(feed, max(rest.POLL_SLEEP_S, retry_s))
                    backoff = min(rest.MAX_BACKOFF_S, max(rest.POLL_SLEEP_S, backoff * 2.0))
                    continue
                if response.status_code != 200:
                    stats.kalshi_reconnects += 1
                    await _sleep_for_feed(feed, min(rest.MAX_BACKOFF_S, max(0.25, backoff)))
                    continue

                top = rest._top_from_rest(ticker, response.json(), request_start_ms)
                if top is not None:
                    receive_ms = int(time.time() * 1000)
                    stats.events += 1
                    stats.kalshi_events += 1
                    rest.hot._enqueue(
                        feed.raw_queue,
                        ("kalshi", receive_ms, None, top, mono_ns),
                        stats,
                    )
                backoff = rest.POLL_SLEEP_S
                cadence = rest.POLL_SLEEP_S if bool(feed.active) else PASSIVE_POLL_SLEEP_S
                await _sleep_for_feed(feed, cadence)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                stats.kalshi_reconnects += 1
                rest.logger.warning(
                    "CRYPTO_KALSHI_REST_BOOK_ERROR asset=%s ticker=%s active=%s type=%s error=%s backoff_s=%.3f",
                    stats.asset, ticker, bool(feed.active), type(exc).__name__, str(exc)[:180], backoff,
                )
                await _sleep_for_feed(feed, backoff)
                backoff = min(rest.MAX_BACKOFF_S, max(rest.POLL_SLEEP_S, backoff * 2.0))


async def _start_feed_paced(*, asset: str, pair: Any, stats: Any, active: bool):
    boundary_ms = int((pair.window_end.timestamp() - 900.0) * 1000)
    feed = rest.base.WarmFeed(
        asset=asset,
        pair=pair,
        stats=stats,
        boundary_ms=boundary_ms,
        active=active,
    )
    feed.stream_tasks = [
        asyncio.create_task(
            rest._gemini_shared_stream(pair.gemini_symbol, feed.raw_queue, stats),
            name=f"hot-gemini-shared-{asset}-{int(pair.window_end.timestamp())}",
        ),
        asyncio.create_task(
            _paced_rest_book_stream(feed),
            name=f"hot-kalshi-rest-paced-{asset}-{int(pair.window_end.timestamp())}",
        ),
    ]
    feed.pump_task = asyncio.create_task(
        rest.base._pump_feed(feed),
        name=f"hot-pump-{asset}-{int(pair.window_end.timestamp())}",
    )
    rest.logger.info(
        "CRYPTO_HOT_DOUBLE_BUFFER_FEED_START %s",
        json.dumps({
            "asset": asset,
            "gemini_symbol": pair.gemini_symbol,
            "kalshi_ticker": pair.kalshi_ticker,
            "window_end_utc": pair.window_end.isoformat(),
            "active": active,
            "gemini_transport": "ONE_SHARED_WS_DEPTH_RPC_PLUS_BASIS_SPOT",
            "kalshi_transport": "REST_EXACT_ORDERBOOK_ACTIVE_FAST_PASSIVE_PACED",
            "kalshi_active_poll_s": rest.POLL_SLEEP_S,
            "kalshi_passive_poll_s": PASSIVE_POLL_SLEEP_S,
            "activation_check_s": ACTIVATION_CHECK_S,
            "quote_timestamps": "REQUEST_START_CONSERVATIVE",
            "risk_gates_changed": False,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return feed


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # rest.install() must run first; replace only its feed constructor afterward.
    rest.base._start_feed = _start_feed_paced
    _INSTALLED = True
    rest.logger.warning(
        "CRYPTO_KALSHI_PASSIVE_PREWARM_PACING_INSTALLED active_poll_s=%.2f "
        "passive_poll_s=%.2f activation_check_s=%.2f exact_books=true "
        "risk_gates_changed=false",
        rest.POLL_SLEEP_S,
        PASSIVE_POLL_SLEEP_S,
        ACTIVATION_CHECK_S,
    )

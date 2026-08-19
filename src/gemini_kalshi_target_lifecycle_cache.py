"""Fail-closed Kalshi 15m strike cache for the live crypto campaign.

Primary source: Kalshi ``market_lifecycle_v2`` WebSocket metadata updates. Kalshi's
current API emits ``metadata_updated`` when ``floor_strike`` changes. We keep one
stable authenticated lifecycle socket, watch the exact current/next BTC/ETH/SOL/XRP
tickers, and cache only an authoritative numeric floor strike for an exact ticker.

Fallback source: ``GET /markets/{ticker}``, rate-limited with per-ticker exponential
backoff. The fallback exists for process starts after a lifecycle event was already
published and for lifecycle reconnects. It never infers or manufactures a strike.

This module changes target acquisition timing only. It does not alter quantity,
profit, boundary, freshness, skew, depth, or execution gates.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import json
import logging
import time
from typing import Any

import httpx
import websockets

from src import gemini_kalshi_micro_live_readiness_20bp as readiness
from src import gemini_kalshi_predictive_hot as hot
from src.gemini_kalshi_predictive_hot import predicted_pair

basis = readiness.basis
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_target_lifecycle_cache")

REST_INITIAL_BACKOFF_S = 1.0
REST_MAX_BACKOFF_S = 8.0
REST_PREFETCH_LEAD_S = 20.0
LIFECYCLE_RECONNECT_INITIAL_S = 0.5
LIFECYCLE_RECONNECT_MAX_S = 8.0

# Exact ticker -> (target, source, receive_ms)
_TARGET_BY_TICKER: dict[str, tuple[Decimal, str, int]] = {}
_WATCHED_TICKERS: set[str] = set()
_INSTALLED = False


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() and out > 0 else None


def extract_lifecycle_target(payload: dict[str, Any]) -> tuple[str, Decimal] | None:
    """Extract only an exact market ticker + authoritative numeric floor strike.

    Kalshi's lifecycle schema has evolved, so accept the documented top-level
    ``floor_strike`` shape as well as the common nested ``msg`` envelope. Never parse
    titles/subtitles or derive a value from text.
    """
    if not isinstance(payload, dict):
        return None
    msg = payload.get("msg") if isinstance(payload.get("msg"), dict) else {}
    ticker = str(
        msg.get("market_ticker")
        or msg.get("ticker")
        or payload.get("market_ticker")
        or payload.get("ticker")
        or ""
    ).upper().strip()
    target = _dec(
        msg.get("floor_strike")
        if msg.get("floor_strike") is not None
        else payload.get("floor_strike")
    )
    if not ticker or target is None:
        return None
    return ticker, target


def _remember(ticker: str, target: Decimal, source: str) -> None:
    ticker = str(ticker).upper().strip()
    if not ticker or target <= 0:
        return
    prior = _TARGET_BY_TICKER.get(ticker)
    row = (D(target), str(source), int(time.time() * 1000))
    _TARGET_BY_TICKER[ticker] = row
    if prior is None or prior[0] != row[0] or prior[1] != row[1]:
        logger.info(
            "CRYPTO_TARGET_CACHE_UPDATE %s",
            json.dumps({
                "ticker": ticker,
                "target": str(target),
                "source": source,
                "watched": ticker in _WATCHED_TICKERS,
                "order_entry": False,
            }, separators=(",", ":")),
        )


def _target_for_ticker(ticker: str) -> tuple[Decimal, str, int] | None:
    return _TARGET_BY_TICKER.get(str(ticker).upper().strip())


async def _lifecycle_stream() -> None:
    """One stable lifecycle socket; reconnect with backoff and never place orders."""
    private_key = hot.load_private_key()
    backoff = LIFECYCLE_RECONNECT_INITIAL_S
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
                await ws.send(json.dumps({
                    "id": 9101,
                    "cmd": "subscribe",
                    "params": {"channels": ["market_lifecycle_v2"]},
                }, separators=(",", ":")))
                logger.info(
                    "CRYPTO_TARGET_LIFECYCLE_CONNECTED channel=market_lifecycle_v2 order_entry=false"
                )
                backoff = LIFECYCLE_RECONNECT_INITIAL_S
                async for raw in ws:
                    payload = json.loads(raw)
                    parsed = extract_lifecycle_target(payload)
                    if parsed is None:
                        continue
                    ticker, target = parsed
                    if ticker in _WATCHED_TICKERS:
                        _remember(ticker, target, "KALSHI_MARKET_LIFECYCLE_V2")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "CRYPTO_TARGET_LIFECYCLE_RECONNECT type=%s error=%s backoff_s=%.2f",
                type(exc).__name__,
                str(exc)[:200],
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, LIFECYCLE_RECONNECT_MAX_S)


async def _rest_target(
    http: httpx.AsyncClient,
    *,
    asset: str,
    pair: Any,
    window_iso: str,
) -> tuple[str, Decimal | None, int]:
    """Single authoritative fallback lookup; caller controls all retry timing."""
    try:
        response = await http.get(
            f"{readiness.KALSHI_REST}/markets/{pair.kalshi_ticker}", timeout=5.0
        )
        status = int(response.status_code)
        if status != 200:
            return asset, None, status
        market = response.json().get("market") or {}
        target = readiness.kalshi_target(market)
        if target is not None:
            _remember(pair.kalshi_ticker, target, "KALSHI_REST_FALLBACK")
        return asset, target, status
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "CRYPTO_TARGET_REST_ERROR asset=%s window=%s type=%s error=%s",
            asset,
            window_iso,
            type(exc).__name__,
            str(exc)[:160],
        )
        return asset, None, 0


async def _kalshi_target_loop_lifecycle(assets: tuple[str, ...]) -> None:
    requested = tuple(dict.fromkeys(
        str(asset).upper()
        for asset in assets
        if str(asset).upper() in basis.ASSET_SYMBOL
    )) or ("BTC",)
    lifecycle_task = asyncio.create_task(
        _lifecycle_stream(), name="kalshi-target-lifecycle-v2"
    )
    next_rest_at: dict[str, float] = {}
    rest_backoff: dict[str, float] = {}
    last_window_iso: str | None = None
    last_logged_state: tuple[str, tuple[str, ...], tuple[str, ...]] | None = None

    limits = httpx.Limits(max_connections=6, max_keepalive_connections=4)
    try:
        async with httpx.AsyncClient(timeout=5.0, limits=limits) as http:
            while True:
                now = time.time()
                window_end = basis._next_window_end()
                window_iso = window_end.isoformat()
                next_end = window_end + timedelta(minutes=15)
                next_iso = next_end.isoformat()

                current_pairs = {asset: predicted_pair(asset) for asset in requested}
                next_pairs = {
                    asset: predicted_pair(asset, now_ts=window_end.timestamp() + 0.001)
                    for asset in requested
                }
                _WATCHED_TICKERS.clear()
                _WATCHED_TICKERS.update(
                    str(pair.kalshi_ticker).upper()
                    for pair in (*current_pairs.values(), *next_pairs.values())
                )

                if window_iso != last_window_iso:
                    # Drop stale readiness rows, then atomically promote any exact
                    # current-ticker values already learned from lifecycle/REST.
                    readiness._KALSHI_TARGETS.clear()
                    for asset, pair in current_pairs.items():
                        cached = _target_for_ticker(pair.kalshi_ticker)
                        if cached is not None:
                            readiness._KALSHI_TARGETS[asset] = (window_iso, cached[0])
                    promoted = sorted(readiness._KALSHI_TARGETS)
                    logger.info(
                        "CRYPTO_TARGET_ROLLOVER_PROMOTION %s",
                        json.dumps({
                            "window_end_utc": window_iso,
                            "assets": promoted,
                            "promotion_after_window_start_ms": max(
                                0,
                                int(now * 1000)
                                - int((window_end - timedelta(minutes=15)).timestamp() * 1000),
                            ),
                            "source_mode": "LIFECYCLE_WS_CACHE_FIRST",
                            "order_entry": False,
                        }, separators=(",", ":")),
                    )
                    last_window_iso = window_iso
                    last_logged_state = None

                # Memory-first current target fill. This is the critical path and has
                # no network call when lifecycle already delivered the strike.
                for asset, pair in current_pairs.items():
                    if readiness._KALSHI_TARGETS.get(asset, (None,))[0] == window_iso:
                        continue
                    cached = _target_for_ticker(pair.kalshi_ticker)
                    if cached is not None:
                        readiness._KALSHI_TARGETS[asset] = (window_iso, cached[0])

                available = sorted(
                    asset for asset in requested
                    if readiness._KALSHI_TARGETS.get(asset, (None,))[0] == window_iso
                )
                missing = sorted(set(requested) - set(available))

                # REST is fallback only. At most one request per ticker per backoff
                # interval; 429s double the interval instead of creating a request storm.
                due: list[tuple[str, Any, str]] = []
                for asset in missing:
                    pair = current_pairs[asset]
                    ticker = str(pair.kalshi_ticker).upper()
                    if now >= next_rest_at.get(ticker, 0.0):
                        due.append((asset, pair, window_iso))
                seconds_to_rollover = window_end.timestamp() - now
                if seconds_to_rollover <= REST_PREFETCH_LEAD_S:
                    for asset, pair in next_pairs.items():
                        ticker = str(pair.kalshi_ticker).upper()
                        if _target_for_ticker(ticker) is None and now >= next_rest_at.get(ticker, 0.0):
                            due.append((asset, pair, next_iso))

                if due:
                    rows = await asyncio.gather(*(
                        _rest_target(http, asset=asset, pair=pair, window_iso=iso)
                        for asset, pair, iso in due
                    ))
                    for (asset, pair, iso), (_a, target, status) in zip(due, rows):
                        ticker = str(pair.kalshi_ticker).upper()
                        if target is not None:
                            next_rest_at.pop(ticker, None)
                            rest_backoff.pop(ticker, None)
                            if iso == window_iso:
                                readiness._KALSHI_TARGETS[asset] = (window_iso, target)
                            continue
                        previous = rest_backoff.get(ticker, REST_INITIAL_BACKOFF_S)
                        backoff = min(
                            REST_MAX_BACKOFF_S,
                            previous * 2.0 if status == 429 else max(REST_INITIAL_BACKOFF_S, previous),
                        )
                        rest_backoff[ticker] = backoff
                        next_rest_at[ticker] = time.time() + backoff
                        if status == 429:
                            logger.warning(
                                "CRYPTO_TARGET_REST_429 ticker=%s backoff_s=%.2f",
                                ticker,
                                backoff,
                            )

                available = sorted(
                    asset for asset in requested
                    if readiness._KALSHI_TARGETS.get(asset, (None,))[0] == window_iso
                )
                missing = sorted(set(requested) - set(available))
                state = (window_iso, tuple(available), tuple(missing))
                if state != last_logged_state:
                    sources = {
                        asset: (
                            _target_for_ticker(current_pairs[asset].kalshi_ticker)[1]
                            if _target_for_ticker(current_pairs[asset].kalshi_ticker)
                            else None
                        )
                        for asset in available
                    }
                    logger.info(
                        "CRYPTO_MICRO_READY_TARGET_CONTEXT %s",
                        json.dumps({
                            "window_end_utc": window_iso,
                            "assets_with_kalshi_target": available,
                            "missing_assets": missing,
                            "retrying_missing_targets": bool(missing),
                            "target_sources": sources,
                            "target_fetch_mode": "KALSHI_LIFECYCLE_WS_WITH_BACKOFF_REST_FALLBACK",
                            "minimum_double_boundary_bps": str(readiness.MIN_DOUBLE_BOUNDARY_BPS),
                            "trade_authorized": False,
                            "read_only": True,
                            "order_entry": False,
                        }, separators=(",", ":")),
                    )
                    last_logged_state = state

                # Tight memory-only boundary promotion loop, without tight REST polling.
                distance = abs(window_end.timestamp() - time.time())
                await asyncio.sleep(0.01 if distance <= 1.5 else 0.05)
    finally:
        lifecycle_task.cancel()
        await asyncio.gather(lifecycle_task, return_exceptions=True)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    readiness._kalshi_target_loop = _kalshi_target_loop_lifecycle
    _INSTALLED = True
    logger.warning(
        "CRYPTO_TARGET_LIFECYCLE_CACHE_INSTALLED primary=market_lifecycle_v2 fallback=rest_exponential_backoff risk_gates_changed=false"
    )

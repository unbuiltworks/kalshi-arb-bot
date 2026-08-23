"""Kalshi REST transport guard for the one-shot maker LIVE_PROBE.

The account currently rate-limits additional authenticated Kalshi WebSocket
handshakes.  The maker lane does not need a Kalshi websocket to remain safe:
Gemini remains streamed, while Kalshi top-of-book and the single resting order's
account state can be polled through authenticated REST.

This module only replaces the two Kalshi stream functions used by the isolated
maker process.  It does not alter authorization, quantity, profit, freshness,
post-only, cancel/reconcile, hedge, flatten, or settlement-risk gates.

Conservative details:
- exact GET /markets/{ticker}/orderbook?depth=1 only;
- book timestamp is HTTP request start, never response arrival;
- one-sided binary books retain zero depth on the missing side;
- 429/error/parse failure emits no market quote;
- account readiness is set only after an authoritative GET /portfolio/orders;
- account polling emits only changed order snapshots; final fill authority remains
  the existing GET /portfolio/fills reconciliation in the maker state machine.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any

from src.auth import KalshiClient
from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_probe_transport import KALSHI_PROD_REST

D = Decimal
ONE = D("1")
logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_rest_guard")
BOOK_POLL_ENV = "MAKER_KALSHI_BOOK_REST_POLL_S"
ACCOUNT_POLL_ENV = "MAKER_KALSHI_ACCOUNT_REST_POLL_S"
DEFAULT_BOOK_POLL_S = 0.18
DEFAULT_ACCOUNT_POLL_S = 0.18
MIN_POLL_S = 0.10
MAX_POLL_S = 0.50
_INSTALLED = False


def _poll_seconds(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except Exception:
        value = default
    return max(MIN_POLL_S, min(value, MAX_POLL_S))


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _best_bid(rows: Any) -> tuple[Decimal, Decimal] | None:
    if not isinstance(rows, list):
        return None
    best: tuple[Decimal, Decimal] | None = None
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        price = _d(row[0])
        qty = _d(row[1])
        if price is None or qty is None or qty <= 0:
            continue
        if best is None or price > best[0]:
            best = (price, qty)
    return best


def exact_rest_top(payload: dict[str, Any], ticker: str, request_start_ms: int) -> TopOfBook | None:
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    yes = _best_bid(book.get("yes_dollars") or [])
    no = _best_bid(book.get("no_dollars") or [])
    if yes is None and no is None:
        return None
    yes_bid, yes_bid_size = yes if yes is not None else (D("0"), D("0"))
    if no is None:
        yes_ask, yes_ask_size = ONE, D("0")
    else:
        no_bid, no_bid_size = no
        yes_ask, yes_ask_size = ONE - no_bid, no_bid_size
    if not (D("0") <= yes_bid <= ONE and D("0") <= yes_ask <= ONE):
        return None
    if yes_bid > yes_ask:
        return None
    return TopOfBook(
        venue="kalshi",
        market_id=str(ticker),
        ts_ms=int(request_start_ms),
        yes_bid=yes_bid,
        yes_bid_size=yes_bid_size,
        yes_ask=yes_ask,
        yes_ask_size=yes_ask_size,
    )


def _enqueue(queue: asyncio.Queue, item: tuple[str, int, Any]) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait(item)


async def kalshi_rest_book_stream(ticker: str, queue: asyncio.Queue, stats: Any) -> None:
    client = KalshiClient(base_url=KALSHI_PROD_REST)
    poll_s = _poll_seconds(BOOK_POLL_ENV, DEFAULT_BOOK_POLL_S)
    backoff_until = 0.0
    logger.warning(
        "MAKER_KALSHI_REST_BOOK_ENABLED %s",
        json.dumps({
            "ticker": ticker,
            "poll_ms": int(poll_s * 1000),
            "depth": 1,
            "quote_timestamp": "HTTP_REQUEST_START",
            "websocket": False,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    try:
        while True:
            if time.monotonic() < backoff_until:
                await asyncio.sleep(min(0.05, backoff_until - time.monotonic()))
                continue
            request_start_ms = int(time.time() * 1000)
            try:
                response = await client.get(f"/markets/{ticker}/orderbook", params={"depth": 1})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("MAKER_KALSHI_REST_BOOK_ERROR ticker=%s type=%s", ticker, type(exc).__name__)
                await asyncio.sleep(poll_s)
                continue
            if response.status_code == 429:
                raw = response.headers.get("retry-after", "").strip()
                try:
                    retry = float(raw) if raw else 0.50
                except ValueError:
                    retry = 0.50
                backoff_until = time.monotonic() + max(0.25, retry)
                logger.warning("MAKER_KALSHI_REST_BOOK_429 ticker=%s backoff_s=%.3f", ticker, max(0.25, retry))
                continue
            if response.status_code == 200:
                try:
                    top = exact_rest_top(response.json(), ticker, request_start_ms)
                except Exception:
                    top = None
                if top is not None:
                    receive_ms = int(time.time() * 1000)
                    _enqueue(queue, ("kalshi", receive_ms, top))
            await asyncio.sleep(poll_s)
    finally:
        await client.close()


def _order_snapshot(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("fill_count_fp") or row.get("fill_count") or "0"),
        str(row.get("status") or ""),
        str(row.get("remaining_count_fp") or row.get("remaining_count") or ""),
    )


def _user_order_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "user_order",
        "msg": {
            "order_id": row.get("order_id"),
            "client_order_id": row.get("client_order_id"),
            "fill_count_fp": row.get("fill_count_fp") or row.get("fill_count") or "0",
            "status": row.get("status"),
        },
    }


async def kalshi_rest_account_stream(ticker: str, queue: asyncio.Queue, ready: asyncio.Event) -> None:
    client = KalshiClient(base_url=KALSHI_PROD_REST)
    poll_s = _poll_seconds(ACCOUNT_POLL_ENV, DEFAULT_ACCOUNT_POLL_S)
    seen: dict[str, tuple[str, str, str]] = {}
    backoff_until = 0.0
    logger.warning(
        "MAKER_KALSHI_REST_ACCOUNT_ENABLED %s",
        json.dumps({
            "ticker": ticker,
            "poll_ms": int(poll_s * 1000),
            "endpoint": "/portfolio/orders",
            "websocket": False,
            "fill_authority": "POST_CANCEL_GET_PORTFOLIO_FILLS_RECONCILIATION",
            "order_entry": False,
        }, separators=(",", ":")),
    )
    try:
        while True:
            if time.monotonic() < backoff_until:
                await asyncio.sleep(min(0.05, backoff_until - time.monotonic()))
                continue
            try:
                response = await client.get("/portfolio/orders", params={"ticker": ticker, "limit": 100})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("MAKER_KALSHI_REST_ACCOUNT_ERROR ticker=%s type=%s", ticker, type(exc).__name__)
                await asyncio.sleep(poll_s)
                continue
            if response.status_code == 429:
                raw = response.headers.get("retry-after", "").strip()
                try:
                    retry = float(raw) if raw else 0.50
                except ValueError:
                    retry = 0.50
                backoff_until = time.monotonic() + max(0.25, retry)
                logger.warning("MAKER_KALSHI_REST_ACCOUNT_429 ticker=%s backoff_s=%.3f", ticker, max(0.25, retry))
                continue
            if response.status_code != 200:
                await asyncio.sleep(poll_s)
                continue
            rows = list(response.json().get("orders") or [])
            current: dict[str, tuple[str, str, str]] = {}
            for row in rows:
                client_id = str(row.get("client_order_id") or "")
                if not client_id:
                    continue
                snapshot = _order_snapshot(row)
                current[client_id] = snapshot
                prior = seen.get(client_id)
                if prior is not None and prior != snapshot:
                    await queue.put(_user_order_event(row))
                elif ready.is_set() and prior is None and client_id.startswith("makerprobe-"):
                    # The isolated live process creates at most one new makerprobe order.
                    await queue.put(_user_order_event(row))
            seen.update(current)
            if not ready.is_set():
                ready.set()
            await asyncio.sleep(poll_s)
    finally:
        await client.close()


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from src import gemini_kalshi_maker_live_probe_q1 as base

    base._kalshi_stream = kalshi_rest_book_stream
    base._account_stream = kalshi_rest_account_stream
    _INSTALLED = True
    logger.warning(
        "MAKER_KALSHI_REST_GUARD_INSTALLED book_ws=false account_ws=false exact_depth1=true "
        "order_polling=true risk_gates_changed=false"
    )

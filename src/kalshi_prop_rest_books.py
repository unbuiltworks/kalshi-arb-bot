"""Rate-bounded Kalshi REST top-of-book transport for MLB prop live canaries.

Purpose: keep the authoritative Gemini/Kalshi MLB prop canary operational when the
account's authenticated Kalshi websocket slot is occupied by another strategy.
Discovery polls nested event top-of-book snapshots by series.  A quote request with
an ultra-fresh max age (<=100 ms) forces an exact depth=1 orderbook fetch once the
cached snapshot is older than 40 ms, so the existing 50 ms second revalidation uses
fresh exact Kalshi liquidity before either FOK order is submitted.

This module is read-only. It contains no order transport.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any, Mapping

import httpx

from src.eventing_books import BookUpdate
from src.kalshi_cross_book_v2 import TwoSidedQuote
from src.strict_mlb_prop_snapshot import KALSHI, kalshi_top_book

logger = logging.getLogger("kalshi_arb.kalshi_prop_rest_books")
D = Decimal
ONE = D("1")
SERIES = ("KXMLBHIT", "KXMLBHR", "KXMLBTB", "KXMLBKS")


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _nested_quote(market: Mapping[str, Any], received_ms: int) -> TwoSidedQuote | None:
    ticker = str(market.get("ticker") or "")
    yes_ask = _d(market.get("yes_ask_dollars"))
    yes_depth = _d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
    yes_bid = _d(market.get("yes_bid_dollars"))
    yes_bid_depth = _d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
    if not ticker or None in (yes_ask, yes_depth, yes_bid, yes_bid_depth):
        return None
    assert yes_ask is not None and yes_depth is not None and yes_bid is not None and yes_bid_depth is not None
    if str(market.get("status") or "").lower() not in {"active", "open"}:
        return None
    no_ask = ONE - yes_bid
    if not (D("0") < yes_ask <= ONE and D("0") < no_ask <= ONE):
        return None
    if yes_depth <= 0 or yes_bid_depth <= 0:
        return None
    return TwoSidedQuote(
        ticker=ticker,
        yes_ask=yes_ask,
        yes_depth=yes_depth,
        no_ask=no_ask,
        no_depth=yes_bid_depth,
        received_ms=received_ms,
        exchange_ts_ms=None,
        seq=None,
    )


class KalshiPropRestBooks:
    """Drop-in subset of EventingKalshiCrossBooks used by the live prop canary."""

    def __init__(self, tickers: list[str] | tuple[str, ...], *, max_queue: int = 20000, **_: Any) -> None:
        self.tickers = tuple(dict.fromkeys(str(x) for x in tickers if x))
        if not self.tickers:
            raise ValueError("at least one Kalshi ticker is required")
        self._wanted = set(self.tickers)
        self._quotes: dict[str, TwoSidedQuote] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._http: httpx.AsyncClient | None = None
        self.updates: asyncio.Queue[BookUpdate] = asyncio.Queue(maxsize=max_queue)
        self.dropped_updates = 0
        self.rest_series_requests = 0
        self.exact_book_requests = 0
        self.rate_limits = 0

    @property
    def initialized_count(self) -> int:
        return len(self._quotes)

    def _notify(self, ticker: str, kind: str, received_ms: int) -> None:
        row = BookUpdate("kalshi", ticker, received_ms, time.perf_counter_ns(), kind)
        try:
            self.updates.put_nowait(row)
        except asyncio.QueueFull:
            try:
                self.updates.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped_updates += 1
            try:
                self.updates.put_nowait(row)
            except asyncio.QueueFull:
                self.dropped_updates += 1

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        assert self._http is not None
        response = await self._http.get(f"{KALSHI}{path}", **kwargs)
        if response.status_code == 429:
            self.rate_limits += 1
        return response

    async def _refresh_series(self, series: str) -> None:
        for attempt in range(3):
            response = await self._get(
                "/events",
                params={"limit": 200, "status": "open", "series_ticker": series, "with_nested_markets": "true"},
            )
            self.rest_series_requests += 1
            if response.status_code == 429:
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            if response.status_code != 200:
                return
            body = response.json() if response.content else {}
            received_ms = int(time.time() * 1000)
            fresh: list[TwoSidedQuote] = []
            for event in body.get("events") or []:
                if not isinstance(event, Mapping):
                    continue
                for market in event.get("markets") or []:
                    if not isinstance(market, Mapping):
                        continue
                    ticker = str(market.get("ticker") or "")
                    if ticker not in self._wanted:
                        continue
                    q = _nested_quote(market, received_ms)
                    if q is not None:
                        fresh.append(q)
            async with self._lock:
                for q in fresh:
                    self._quotes[q.ticker] = q
            for q in fresh:
                self._notify(q.ticker, "rest_series_snapshot", received_ms)
            return

    async def _exact_quote(self, ticker: str) -> TwoSidedQuote | None:
        response = await self._get(f"/markets/{ticker}/orderbook", params={"depth": 1})
        self.exact_book_requests += 1
        if response.status_code != 200:
            return None
        body = response.json() if response.content else {}
        book = kalshi_top_book(body)
        if book is None:
            return None
        received_ms = int(time.time() * 1000)
        q = TwoSidedQuote(
            ticker=ticker,
            yes_ask=book.yes_ask,
            yes_depth=book.yes_depth,
            no_ask=book.no_ask,
            no_depth=book.no_depth,
            received_ms=received_ms,
            exchange_ts_ms=None,
            seq=None,
        )
        async with self._lock:
            self._quotes[ticker] = q
        return q

    async def quote(self, ticker: str, *, max_age_ms: int = 30000) -> TwoSidedQuote | None:
        now = int(time.time() * 1000)
        async with self._lock:
            cached = self._quotes.get(ticker)
        age = now - cached.received_ms if cached is not None else 10**9
        # For the live canary's <=100 ms gate, cached series snapshots are good for
        # trigger/evaluation, but the second check after its 50 ms sleep must become
        # an exact depth=1 fetch once the snapshot is older than 40 ms.
        if max_age_ms <= 100 and age > 40:
            cached = await self._exact_quote(ticker)
            if cached is None:
                return None
            age = int(time.time() * 1000) - cached.received_ms
        if cached is None or age > max_age_ms:
            return None
        return cached

    async def run(self) -> None:
        self._running = True
        self._http = httpx.AsyncClient(timeout=5.0)
        logger.warning("KALSHI_PROP_BOOK_TRANSPORT %s", json.dumps({
            "mode": "REST_SERIES_SNAPSHOT_PLUS_EXACT_REVALIDATION",
            "tickers": len(self.tickers),
            "series": list(SERIES),
            "poll_spacing_seconds": 0.25,
            "exact_revalidation_after_cached_age_ms": 40,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
        try:
            while self._running:
                for series in SERIES:
                    if not self._running:
                        break
                    try:
                        await self._refresh_series(series)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning("KALSHI_PROP_REST_REFRESH_ERROR series=%s type=%s", series, type(exc).__name__)
                    await asyncio.sleep(0.25)
        finally:
            if self._http is not None:
                await self._http.aclose()
                self._http = None

    async def close(self) -> None:
        self._running = False
        if self._http is not None:
            await self._http.aclose()
            self._http = None

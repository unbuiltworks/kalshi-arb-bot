"""Warm pair-catalog entrypoint for the read-only crypto HOT scanner.

The hot market-data loop must not stop at a 15-minute boundary and then discover
what to subscribe to next. This compatibility wrapper replaces the on-demand pair
cache with a background catalog that refreshes while current venue WebSockets are
running. At rollover, the next aligned pair is normally already in memory.

This changes discovery timing only. The underlying hot scanner remains read-only and
continues to classify every Gemini/Kalshi quote edge as INDEX_MISMATCH_RESEARCH_ONLY.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from src.gemini_kalshi_aligned_watch import Pair, _discover_pairs
from src import gemini_kalshi_multi_ws_hot as hot

logger = logging.getLogger("kalshi_arb.gemini_kalshi_multi_ws_hot_catalog")


class BackgroundPairCatalog:
    """Continuously refresh aligned pair identities outside the hot rollover path."""

    def __init__(self, ttl_s: float = 1.0, *, refresh_s: float = 5.0) -> None:
        # ``ttl_s`` is accepted for drop-in compatibility with PairDiscoveryCache.
        self.refresh_s = max(1.0, float(refresh_s))
        self._lock = asyncio.Lock()
        # Network discovery is separately single-flighted. Holding _lock across REST
        # would block readers; _refresh_lock prevents four asset workers from issuing
        # the same discovery burst at rollover.
        self._refresh_lock = asyncio.Lock()
        self._pairs: list[Pair] = []
        self._fetched_monotonic = 0.0
        self._task: asyncio.Task[Any] | None = None
        self._refreshes = 0
        self._errors = 0
        self._coalesced_refresh_waiters = 0

    @staticmethod
    def _future_pairs(pairs: list[Pair], asset: str, now: float) -> list[Pair]:
        return [
            p for p in pairs
            if p.asset == asset and p.window_end.timestamp() > now
        ]

    def _log_catalog(self) -> None:
        now = time.time()
        by_asset: dict[str, list[dict[str, Any]]] = {}
        for asset in hot.ASSET_SERIES:
            future = self._future_pairs(self._pairs, asset, now)
            by_asset[asset] = [
                {
                    "window_end_utc": p.window_end.isoformat(),
                    "gemini_symbol": p.gemini_symbol,
                    "kalshi_ticker": p.kalshi_ticker,
                    "seconds_ahead": round(p.window_end.timestamp() - now, 3),
                }
                for p in future[:3]
            ]
        logger.info(
            "CRYPTO_HOT_PAIR_CATALOG %s",
            json.dumps({
                "refreshes": self._refreshes,
                "pair_count": len(self._pairs),
                "future_by_asset": by_asset,
                "refresh_s": self.refresh_s,
                "coalesced_refresh_waiters": self._coalesced_refresh_waiters,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")),
        )

    async def _refresh(self, http: httpx.AsyncClient, *, min_spacing_s: float = 0.0) -> None:
        """Refresh once, coalescing concurrent callers and optionally pacing retries."""
        if self._refresh_lock.locked():
            self._coalesced_refresh_waiters += 1
        async with self._refresh_lock:
            if min_spacing_s > 0:
                async with self._lock:
                    age = time.monotonic() - self._fetched_monotonic
                if age < min_spacing_s:
                    return
            pairs = await _discover_pairs(http, 30.0)
            async with self._lock:
                self._pairs = pairs
                self._fetched_monotonic = time.monotonic()
                self._refreshes += 1
                self._log_catalog()

    async def _refresh_loop(self, http: httpx.AsyncClient) -> None:
        while True:
            await asyncio.sleep(self.refresh_s)
            try:
                await self._refresh(http)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._errors += 1
                logger.warning(
                    "CRYPTO_HOT_PAIR_CATALOG_ERROR %s",
                    json.dumps({
                        "errors": self._errors,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:240],
                    }, separators=(",", ":")),
                )

    async def _ensure_background(self, http: httpx.AsyncClient) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._refresh_loop(http), name="crypto_pair_catalog"
            )

    async def current_pair(self, http: httpx.AsyncClient, asset: str) -> Pair | None:
        # Populate once. All four asset workers share this catalog and _refresh() is
        # single-flight, so startup and rollover never fan out duplicate REST scans.
        async with self._lock:
            needs_initial = not self._pairs
        if needs_initial:
            try:
                await self._refresh(http)
            except Exception as exc:
                self._errors += 1
                logger.warning(
                    "CRYPTO_HOT_PAIR_INITIAL_REFRESH_ERROR asset=%s error=%s",
                    asset, str(exc)[:240],
                )
        await self._ensure_background(http)

        now = time.time()
        async with self._lock:
            future = self._future_pairs(self._pairs, asset, now)
            if future:
                return future[0]
            age = time.monotonic() - self._fetched_monotonic

        # Some 15-minute contracts are published only at the exact boundary and the
        # four assets do not always appear atomically. Pace missing-asset retries and
        # coalesce them through one network refresh instead of a four-worker burst.
        if age >= 0.35:
            try:
                await self._refresh(http, min_spacing_s=0.35)
            except Exception as exc:
                self._errors += 1
                logger.warning(
                    "CRYPTO_HOT_PAIR_BOUNDARY_REFRESH_ERROR asset=%s error=%s",
                    asset, str(exc)[:240],
                )
            now = time.time()
            async with self._lock:
                future = self._future_pairs(self._pairs, asset, now)
                return future[0] if future else None
        return None


# main() constructs PairDiscoveryCache at runtime, so replacing the module symbol here
# leaves the validated hot book/economics code untouched.
hot.PairDiscoveryCache = BackgroundPairCatalog


if __name__ == "__main__":
    hot.main()

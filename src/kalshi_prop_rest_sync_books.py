"""Candidate-synchronized Kalshi REST books for the G/K live hot path.

The base transport refreshes MLB prop series at a bounded cadence and performs an
exact depth=1 second look only after a candidate is already found. With a 100 ms
book-age gate, a Gemini websocket tick can otherwise miss a valid edge simply
because the latest Kalshi series snapshot is older than 100 ms.

This adapter keeps the base behavior but allows one candidate-triggered exact
depth=1 Kalshi refresh at a globally bounded cadence when the requested live book
is stale. A successful synchronized refresh is marked as the first candidate
look so the existing 35-150 ms second-look logic still performs an independent
exact revalidation before submission.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from src.kalshi_cross_book_v2 import TwoSidedQuote
from src.kalshi_prop_rest_books import KalshiPropRestBooks

logger = logging.getLogger("kalshi_arb.kalshi_prop_rest_sync_books")


class KalshiPropRestSyncBooks(KalshiPropRestBooks):
    """REST series discovery plus bounded Gemini-triggered exact synchronization."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.candidate_sync_min_interval_seconds = max(
            0.10,
            float(os.getenv("SPORTS_KALSHI_CANDIDATE_SYNC_MIN_SECONDS", "0.25")),
        )
        self._last_candidate_sync = 0.0
        self._candidate_sync_lock = asyncio.Lock()
        self.candidate_sync_requests = 0
        self.candidate_sync_hits = 0
        self.candidate_sync_rate_limited = 0

    async def quote(self, ticker: str, *, max_age_ms: int = 30000) -> TwoSidedQuote | None:
        quote = await super().quote(ticker, max_age_ms=max_age_ms)
        if quote is not None:
            return quote

        # Only the ultra-fresh live hot path (100 ms today) may wake an exact
        # synchronization. Slower discovery/diagnostic reads remain cache-only.
        if max_age_ms > 250:
            return None

        async with self._candidate_sync_lock:
            now_mono = time.monotonic()
            elapsed = now_mono - self._last_candidate_sync
            if elapsed < self.candidate_sync_min_interval_seconds:
                self.candidate_sync_rate_limited += 1
                return None
            self._last_candidate_sync = now_mono
            self.candidate_sync_requests += 1

        exact = await self.exact_quote(ticker)
        if exact is None:
            return None

        self.candidate_sync_hits += 1
        # Treat this direct read as the first candidate look. If economics pass,
        # the canary's deliberate 50 ms second read will trigger exact_quote()
        # again through the base adapter, preserving independent final depth.
        async with self._lock:
            self._first_access[ticker] = (exact.received_ms, time.monotonic())
        logger.info(
            "KALSHI_PROP_CANDIDATE_SYNC ticker=%s requests=%d hits=%d rate_limited=%d",
            ticker,
            self.candidate_sync_requests,
            self.candidate_sync_hits,
            self.candidate_sync_rate_limited,
        )
        return exact

    async def run(self) -> None:
        logger.warning(
            "KALSHI_PROP_CANDIDATE_SYNC_POLICY %s",
            json.dumps(
                {
                    "enabled": True,
                    "min_interval_ms": int(self.candidate_sync_min_interval_seconds * 1000),
                    "only_when_requested_max_age_ms_lte": 250,
                    "second_exact_revalidation_preserved": True,
                    "read_only": True,
                    "order_entry": False,
                },
                separators=(",", ":"),
            ),
        )
        await super().run()

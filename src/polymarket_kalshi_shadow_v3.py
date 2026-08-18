"""Rate-limit-safe launcher for the read-only v2 cross-exchange scanner."""

from __future__ import annotations

import asyncio
import logging

import httpx

from src import polymarket_kalshi_shadow_v2 as scanner

logger = logging.getLogger("kalshi_arb.poly_kalshi_shadow_v3")
_original_discover = scanner.discover_matches


async def _discover_with_backoff(http: httpx.AsyncClient):
    delay = 5.0
    while True:
        try:
            return await _original_discover(http)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429:
                raise
            retry_after = exc.response.headers.get("retry-after")
            try:
                server_wait = float(retry_after) if retry_after else 0.0
            except ValueError:
                server_wait = 0.0
            wait_s = max(delay, server_wait)
            logger.warning(
                "Kalshi discovery rate-limited; retrying in %.1fs (no orders are enabled)",
                wait_s,
            )
            await asyncio.sleep(wait_s)
            delay = min(delay * 2, 60.0)


scanner.discover_matches = _discover_with_backoff


if __name__ == "__main__":
    scanner.main()

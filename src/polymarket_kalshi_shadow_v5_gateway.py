"""Read-only v5 K/P census entrypoint with public Polymarket REST seeding.

Polymarket market websocket authentication is unchanged. Only the startup
REST book seed is redirected to the public gateway endpoint already used by
older read-only collectors in this repository. Temporary no-inventory states
are retried in-process so Railway restart policy does not create an API-hammering
crash loop. No order-entry transport is imported or added here.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

import httpx

from src import polymarket_kalshi_shadow_v5 as v5
from src.eventing_books import EventingPolyBookStore

logger = logging.getLogger("kalshi_arb.polymarket_kalshi_shadow_v5_gateway")
POLY_PUBLIC_BOOK_HOST = "https://gateway.polymarket.us"
POLY_PUBLIC_HEADERS = {
    "Origin": "https://polymarket.us",
    "User-Agent": "Mozilla/5.0 arb-census/1.0",
    "Connection": "close",
}
INVENTORY_RETRY_SECONDS = 60
_RETRYABLE_INVENTORY_PREFIXES = (
    "No strict Polymarket/Kalshi matches discovered",
    "No strict pairs have a current two-sided Polymarket book",
)


async def _fetch_poly_book_public(
    http: httpx.AsyncClient,
    store: EventingPolyBookStore,
    slug: str,
    sem: asyncio.Semaphore,
) -> bool:
    url = f"{POLY_PUBLIC_BOOK_HOST}/v1/markets/{slug}/book"
    async with sem:
        try:
            resp = await http.get(url, headers=POLY_PUBLIC_HEADERS)
            resp.raise_for_status()
            return store.ingest(resp.json(), source="rest")
        except Exception as exc:
            logger.warning("Polymarket public seed failed market=%s err=%s", slug, exc)
            return False


def _retryable_inventory_gap(exc: BaseException) -> bool:
    if not isinstance(exc, RuntimeError):
        return False
    message = str(exc)
    return any(message.startswith(prefix) for prefix in _RETRYABLE_INVENTORY_PREFIXES)


def _run_forever(args: argparse.Namespace) -> None:
    while True:
        try:
            asyncio.run(v5.run(args))
            return
        except Exception as exc:
            if not _retryable_inventory_gap(exc):
                raise
            logger.warning(
                "CENSUS_NO_INVENTORY_RETRY wait_s=%d reason=%s",
                INVENTORY_RETRY_SECONDS,
                exc,
            )
            time.sleep(INVENTORY_RETRY_SECONDS)


def main() -> None:
    # v5 remains the authoritative event-driven scanner. Override only its
    # read-only startup seed helper and keep temporary no-inventory retries
    # inside the process; websocket, matching, economics, lifetime, depth,
    # skew and latency telemetry stay unchanged.
    v5._fetch_poly_book = _fetch_poly_book_public
    _run_forever(v5.parse_args())


if __name__ == "__main__":
    main()

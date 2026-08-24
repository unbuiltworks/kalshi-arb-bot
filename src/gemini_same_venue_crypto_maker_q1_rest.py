"""REST-book transport wrapper for same-Gemini q=1 ladder maker execution.

Gemini prediction-market bookTicker snapshots can omit transformed NO-side depth in
ways that make the generic crypto parser fail closed. For same-venue sibling ladders
we do not need cross-venue clock synchronization, so use Gemini's public REST books
as the authoritative maker/hedge market-data source while preserving the proven
private WS order/fill transport in gemini_same_venue_crypto_maker_q1.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import os
import time
from typing import Any, Mapping

import httpx

from src import gemini_same_venue_crypto_ladder_scan as scan
from src import gemini_same_venue_crypto_maker_q1 as base
from src.gemini_kalshi_execution_shadow import TopOfBook

D = Decimal
BASE = "https://api.gemini.com"
POLL_SECONDS = max(0.20, min(1.0, float(os.getenv("GEMINI_SAME_VENUE_BOOK_POLL_S", "0.25"))))
REST_SECONDS = max(60.0, min(600.0, float(os.getenv("GEMINI_SAME_VENUE_REST_SECONDS", "240"))))


def _to_top(symbol: str, book: Mapping[str, Any], ts_ms: int) -> TopOfBook:
    t = scan.top(book)
    yes_bid = t.get("yes_bid") if isinstance(t.get("yes_bid"), D) else D("0")
    yes_bid_qty = t.get("yes_bid_qty") if isinstance(t.get("yes_bid_qty"), D) else D("0")
    yes_ask = t.get("yes_ask") if isinstance(t.get("yes_ask"), D) else D("1")
    yes_ask_qty = t.get("yes_ask_qty") if isinstance(t.get("yes_ask_qty"), D) else D("0")
    return TopOfBook(
        venue="gemini",
        market_id=symbol,
        ts_ms=int(ts_ms),
        yes_bid=yes_bid,
        yes_bid_size=yes_bid_qty,
        yes_ask=yes_ask,
        yes_ask_size=yes_ask_qty,
    )


async def _rest_book_stream(symbol: str, kind: str, queue: asyncio.Queue) -> None:
    backoff = POLL_SECONDS
    async with httpx.AsyncClient(timeout=4.0) as http:
        while True:
            try:
                response = await http.get(
                    f"{BASE}/v1/book/{symbol}",
                    params={"limit_bids": 20, "limit_asks": 20},
                )
                if response.status_code != 200:
                    raise RuntimeError(f"Gemini REST book HTTP {response.status_code}")
                body = response.json()
                if not isinstance(body, Mapping):
                    raise RuntimeError("Gemini REST book is not an object")
                top = _to_top(symbol, body, int(time.time() * 1000))
                try:
                    queue.put_nowait((kind, top))
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    queue.put_nowait((kind, top))
                backoff = POLL_SECONDS
                await asyncio.sleep(POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(2.0, max(POLL_SECONDS, backoff * 2))


async def run():
    base._book_stream = _rest_book_stream
    base.REST_SECONDS = REST_SECONDS
    return await base.run()


if __name__ == "__main__":
    asyncio.run(run())

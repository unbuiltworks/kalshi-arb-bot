"""Atomic REST-book transport for same-Gemini q=1 ladder maker execution.

The base executor starts one public-book task per sibling. This wrapper coordinates
those task calls so only one leader polls BOTH Gemini sibling REST books in the same
cycle, timestamps them together, and emits the pair into the existing safety loop.
Short REST hiccups are tolerated without throwing away queue priority; a prolonged
market-data outage emits an intentionally stale pair so the base executor cancels.
Private Gemini order/fill/FOK/flatten transport is unchanged.
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
API = "https://api.gemini.com"
POLL_SECONDS = max(0.20, min(1.0, float(os.getenv("GEMINI_SAME_VENUE_BOOK_POLL_S", "0.25"))))
REST_SECONDS = max(60.0, min(600.0, float(os.getenv("GEMINI_SAME_VENUE_REST_SECONDS", "240"))))
OUTAGE_CANCEL_SECONDS = max(1.0, min(10.0, float(os.getenv("GEMINI_SAME_VENUE_BOOK_OUTAGE_CANCEL_S", "2.0"))))

# One base _attempt creates one queue and two _book_stream tasks. State is scoped by
# queue identity and disappears naturally when those tasks are cancelled.
_STATES: dict[int, dict[str, Any]] = {}


def _to_top(symbol: str, book: Mapping[str, Any], ts_ms: int) -> TopOfBook:
    t = scan.top(book)
    yes_bid = t.get("yes_bid") if isinstance(t.get("yes_bid"), D) else D("0")
    yes_bid_qty = t.get("yes_bid_qty") if isinstance(t.get("yes_bid_qty"), D) else D("0")
    yes_ask = t.get("yes_ask") if isinstance(t.get("yes_ask"), D) else D("1")
    yes_ask_qty = t.get("yes_ask_qty") if isinstance(t.get("yes_ask_qty"), D) else D("0")
    return TopOfBook(
        venue="gemini", market_id=symbol, ts_ms=int(ts_ms),
        yes_bid=yes_bid, yes_bid_size=yes_bid_qty,
        yes_ask=yes_ask, yes_ask_size=yes_ask_qty,
    )


def _stale_top(symbol: str, ts_ms: int) -> TopOfBook:
    return TopOfBook(
        venue="gemini", market_id=symbol, ts_ms=ts_ms,
        yes_bid=D("0"), yes_bid_size=D("0"),
        yes_ask=D("1"), yes_ask_size=D("0"),
    )


async def _get_book(http: httpx.AsyncClient, symbol: str) -> Mapping[str, Any]:
    r = await http.get(f"{API}/v1/book/{symbol}", params={"limit_bids": 20, "limit_asks": 20})
    if r.status_code != 200:
        raise RuntimeError(f"Gemini REST book {symbol} HTTP {r.status_code}")
    body = r.json()
    if not isinstance(body, Mapping):
        raise RuntimeError(f"Gemini REST book {symbol} not object")
    return body


async def _atomic_book_stream(symbol: str, kind: str, queue: asyncio.Queue) -> None:
    key = id(queue)
    state = _STATES.setdefault(key, {"symbols": {}, "ready": asyncio.Event(), "leader": None})
    state["symbols"][kind] = symbol
    if "maker" in state["symbols"] and "hedge" in state["symbols"]:
        state["ready"].set()

    # Exactly one of the two base tasks is the polling leader. The hedge task only
    # registers its symbol and remains cancellable.
    if state["leader"] is None:
        state["leader"] = kind
    if state["leader"] != kind:
        await asyncio.Future()
        return

    await asyncio.wait_for(state["ready"].wait(), timeout=3.0)
    maker_symbol = state["symbols"]["maker"]
    hedge_symbol = state["symbols"]["hedge"]
    outage_started: float | None = None
    async with httpx.AsyncClient(timeout=3.0) as http:
        while True:
            try:
                maker_book, hedge_book = await asyncio.gather(
                    _get_book(http, maker_symbol), _get_book(http, hedge_symbol)
                )
                ts_ms = int(time.time() * 1000)
                maker_top = _to_top(maker_symbol, maker_book, ts_ms)
                hedge_top = _to_top(hedge_symbol, hedge_book, ts_ms)
                await queue.put(("maker", maker_top))
                await queue.put(("hedge", hedge_top))
                outage_started = None
                await asyncio.sleep(POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                now = time.monotonic()
                if outage_started is None:
                    outage_started = now
                if now - outage_started >= OUTAGE_CANCEL_SECONDS:
                    stale_ms = int(time.time() * 1000) - max(5000, int(OUTAGE_CANCEL_SECONDS * 2000))
                    await queue.put(("maker", _stale_top(maker_symbol, stale_ms)))
                    await queue.put(("hedge", _stale_top(hedge_symbol, stale_ms)))
                    outage_started = None
                await asyncio.sleep(POLL_SECONDS)


async def run():
    base._book_stream = _atomic_book_stream
    base.REST_SECONDS = REST_SECONDS
    return await base.run()


if __name__ == "__main__":
    asyncio.run(run())

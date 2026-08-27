"""Gemini BTC5m q=1 inventory-maker canary v3.

v3 keeps the q=1 inventory strategy/order transport unchanged and replaces the
brittle public depth WebSocket snapshot with Gemini's existing REST-book path.
The repo already uses GET /v1/book/{symbol} for Gemini prediction-market maker
market data because prediction WebSocket snapshots can omit usable depth.

When live-armed, v3 may wait read-only for one eligible BTC5m window instead of
requiring the operator to launch in a narrow timing band. The wait performs only
public discovery/book reads; it cannot reserve capital or place orders. Once a
window has enough time and the existing >=1c entry floor, the underlying one-shot
inventory canary runs once and revalidates everything again before mutation.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import os
import time
from typing import Any, Mapping

import httpx

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src import gemini_same_venue_crypto_ladder_scan as scan

D = Decimal
REST_BASE = "https://api.gemini.com"
REST_ATTEMPTS = 3
MAX_REST_RTT_S = 0.75
LIVE_ENTRY_BUFFER_S = 15.0
WAIT_POLL_S = 5.0
MAX_WAIT_S = 420.0


def _rest_top(book: Mapping[str, Any]) -> tuple[D, D, D, D]:
    parsed = scan.top(book)
    bid = parsed.get("yes_bid")
    bid_size = parsed.get("yes_bid_qty")
    ask = parsed.get("yes_ask")
    ask_size = parsed.get("yes_ask_qty")
    if not all(isinstance(x, D) for x in (bid, bid_size, ask, ask_size)):
        raise RuntimeError("Gemini REST book missing complete top of book")
    assert isinstance(bid, D) and isinstance(bid_size, D)
    assert isinstance(ask, D) and isinstance(ask_size, D)
    if not (D("0") < bid < ask < D("1")):
        raise RuntimeError("Gemini REST book has invalid/crossed top")
    if bid_size <= 0 or ask_size <= 0:
        raise RuntimeError("Gemini REST book top has zero depth")
    return bid, bid_size, ask, ask_size


async def rest_fresh_top(symbol: str, timeout: float = 4.0) -> tuple[D, D, D, D]:
    last_error: Exception | None = None
    request_timeout = max(1.0, min(float(timeout), 4.0))
    async with httpx.AsyncClient(timeout=request_timeout, headers={"User-Agent": "arb-gemini-btc5m-inventory/3.0"}) as http:
        for attempt in range(REST_ATTEMPTS):
            started = time.monotonic()
            try:
                response = await http.get(
                    f"{REST_BASE}/v1/book/{symbol}",
                    params={"limit_bids": 20, "limit_asks": 20},
                    headers={"Cache-Control": "no-cache"},
                )
                rtt_s = time.monotonic() - started
                if response.status_code != 200:
                    raise RuntimeError(f"Gemini REST book HTTP {response.status_code}")
                if rtt_s > MAX_REST_RTT_S:
                    raise RuntimeError(f"Gemini REST book stale RTT {rtt_s:.3f}s")
                body = response.json()
                if not isinstance(body, Mapping):
                    raise RuntimeError("Gemini REST book is not an object")
                return _rest_top(body)
            except Exception as exc:
                last_error = exc
                if attempt + 1 < REST_ATTEMPTS:
                    await asyncio.sleep(0.10 * (attempt + 1))
    raise RuntimeError(f"no fresh Gemini BTC5m REST book after {REST_ATTEMPTS} attempts") from last_error


def entry_window_ready(*, seconds_to_expiry: float, modeled_edge: D) -> bool:
    return bool(
        seconds_to_expiry >= inv.MIN_SECONDS_TO_EXPIRY + LIVE_ENTRY_BUFFER_S
        and modeled_edge >= inv.ENTRY_PAIR_EDGE
    )


async def wait_for_entry_window() -> bool:
    deadline = time.monotonic() + MAX_WAIT_S
    last_reason = "NO_EVENT"
    while time.monotonic() < deadline:
        try:
            event = await base.discover_btc5m()
            if event is None:
                last_reason = "NO_EVENT"
            elif event.seconds_to_expiry < inv.MIN_SECONDS_TO_EXPIRY + LIVE_ENTRY_BUFFER_S:
                last_reason = f"TOO_CLOSE_{event.seconds_to_expiry:.1f}s"
            else:
                bid, _, ask, _ = await rest_fresh_top(event.symbol)
                model = base.pair_model(bid, ask)
                if entry_window_ready(seconds_to_expiry=event.seconds_to_expiry, modeled_edge=model["modeled_edge"]):
                    inv.emit(
                        "LIVE_WINDOW_READY",
                        market=event.ticker,
                        symbol=event.symbol,
                        seconds_to_expiry=event.seconds_to_expiry,
                        modeled_edge=str(model["modeled_edge"]),
                        market_data="GEMINI_REST_BOOK",
                        order_entry=False,
                    )
                    return True
                last_reason = f"EDGE_{model['modeled_edge']}"
        except Exception as exc:
            last_reason = f"PUBLIC_PREFLIGHT_{type(exc).__name__}"
        inv.emit(
            "WAITING_FOR_LIVE_WINDOW",
            reason=last_reason,
            max_wait_seconds=MAX_WAIT_S,
            order_entry=False,
        )
        await asyncio.sleep(WAIT_POLL_S)
    inv.emit("STOP", reason="NO_ELIGIBLE_WINDOW_WITHIN_BOUNDED_WAIT", order_entry=False)
    return False


async def run() -> None:
    # Public market data only. Private maker/order transport remains untouched.
    base.fresh_top = rest_fresh_top

    if inv.live_armed():
        if not await wait_for_entry_window():
            return

    # Underlying canary re-discovers and re-reads the book before reserve/mutation.
    await inv.run()


if __name__ == "__main__":
    asyncio.run(run())

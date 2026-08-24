"""Wait briefly for a realistically fillable partial-safe XRP maker window.

This wrapper does not weaken any canary economics or freshness gate. It only refuses
to call a maker price actionable unless the existing partial-safe quote is at least
the current Gemini best bid. Expensive pair/series discovery is cached for the
bounded campaign so the hot loop does not repeatedly walk Kalshi series endpoints.
The underlying canary still owns capital reservation, one-attempt idempotency,
first-fill freeze, exact hedge/flatten, and hard-halt logic.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
import time
from typing import Any

from src import gemini_kalshi_xrp_maker_live_canary as canary

D = Decimal
_ORIGINAL_CHOOSE_QUOTE = canary.choose_quote
_ORIGINAL_DISCOVER_PAIRS = canary._discover_pairs
_ORIGINAL_FEE_METADATA = canary.base.fetch_series_fee_metadata
WAIT_SECONDS = float(os.getenv("CRYPTO_MAKER_FILL_WAIT_SECONDS", "90"))
RETRY_SECONDS = float(os.getenv("CRYPTO_MAKER_FILL_RETRY_SECONDS", "1.5"))
CACHE_SECONDS = float(os.getenv("CRYPTO_MAKER_DISCOVERY_CACHE_SECONDS", "120"))
_PAIR_CACHE: tuple[float, list[Any]] | None = None
_FEE_CACHE: dict[str, tuple[float, Any]] = {}


def fillable_choose_quote(**kwargs: Any):
    quote = _ORIGINAL_CHOOSE_QUOTE(**kwargs)
    if quote is None:
        return None
    if D(str(quote["maker_quote"])) < D(str(kwargs["maker_best_bid"])):
        return None
    return quote


async def cached_discover_pairs(http: Any, seconds: float):
    global _PAIR_CACHE
    now = time.monotonic()
    if _PAIR_CACHE is not None and now - _PAIR_CACHE[0] <= max(1.0, CACHE_SECONDS):
        return _PAIR_CACHE[1]
    last_exc: Exception | None = None
    for delay in (0.0, 0.35, 0.75):
        if delay:
            await asyncio.sleep(delay)
        try:
            rows = await _ORIGINAL_DISCOVER_PAIRS(http, seconds)
            _PAIR_CACHE = (time.monotonic(), rows)
            return rows
        except Exception as exc:
            last_exc = exc
    assert last_exc is not None
    raise last_exc


async def cached_fee_metadata(http: Any, series: str):
    now = time.monotonic()
    cached = _FEE_CACHE.get(series)
    if cached is not None and now - cached[0] <= max(1.0, CACHE_SECONDS):
        return cached[1]
    last_exc: Exception | None = None
    for delay in (0.0, 0.35, 0.75):
        if delay:
            await asyncio.sleep(delay)
        try:
            value = await _ORIGINAL_FEE_METADATA(http, series)
            _FEE_CACHE[series] = (time.monotonic(), value)
            return value
        except Exception as exc:
            last_exc = exc
    assert last_exc is not None
    raise last_exc


async def main() -> dict[str, Any]:
    canary.choose_quote = fillable_choose_quote
    canary._discover_pairs = cached_discover_pairs
    canary.base.fetch_series_fee_metadata = cached_fee_metadata
    if not canary.live_armed():
        result = await canary.run_once()
        print("CRYPTO_XRP_MAKER_FILL_WAIT " + json.dumps({
            "status": "DISARMED_SINGLE_PREFLIGHT",
            "underlying_status": result.get("status"),
            "order_entry": bool(result.get("order_entry")),
            "discovery_cached": _PAIR_CACHE is not None,
        }, separators=(",", ":")), flush=True)
        return result

    deadline = time.monotonic() + max(1.0, WAIT_SECONDS)
    attempts = 0
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        attempts += 1
        last = await canary.run_once()
        status = str(last.get("status") or "")
        if bool(last.get("order_entry")):
            print("CRYPTO_XRP_MAKER_FILL_WAIT " + json.dumps({
                "status": "LIVE_CAMPAIGN_ATTEMPTED",
                "attempts": attempts,
                "underlying_status": status,
                "order_entry": True,
                "discovery_cached": _PAIR_CACHE is not None,
            }, separators=(",", ":")), flush=True)
            return last
        if status.startswith("HARD_HALT") or status in {
            "DUPLICATE_CAMPAIGN_BLOCKED",
            "MAKER_FILL_QUANTITY_AMBIGUOUS_HARD_HALT",
            "ZERO_FILL_CANCEL_AMBIGUOUS_HARD_HALT",
        }:
            return last
        if not canary.live_armed():
            return last
        await asyncio.sleep(max(0.25, RETRY_SECONDS))

    print("CRYPTO_XRP_MAKER_FILL_WAIT " + json.dumps({
        "status": "NO_FILLABLE_PARTIAL_SAFE_WINDOW_WITHIN_WAIT",
        "attempts": attempts,
        "last_underlying_status": last.get("status"),
        "order_entry": False,
        "realized": False,
        "discovery_cached": _PAIR_CACHE is not None,
    }, separators=(",", ":")), flush=True)
    return last


if __name__ == "__main__":
    asyncio.run(main())

"""Reliable market-data entrypoint for the Gemini BTC5m inventory-maker q=1 canary.

The underlying Gemini depth helper intentionally fails closed when it cannot
obtain a complete bid/ask snapshot. A single 4-second websocket subscription can
occasionally miss the initial depth snapshot even when the feed is healthy.

This wrapper changes market-data acquisition only:
- up to 3 independent websocket subscription attempts;
- each attempt uses the existing 4-second bounded helper;
- short backoff between attempts;
- no REST/stale-price fallback;
- trading, inventory, arm, sizing, reservation and settlement behavior is
  unchanged from gemini_short_crypto_btc5m_inventory_maker_canary_q1.
"""
from __future__ import annotations

import asyncio

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv

_ATTEMPTS = 3
_ATTEMPT_TIMEOUT_SECONDS = 4.0
_RETRY_DELAY_SECONDS = 0.25
_original_fresh_top = inv.base.fresh_top


async def reliable_fresh_top(symbol: str, timeout: float = _ATTEMPT_TIMEOUT_SECONDS):
    last_error: Exception | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return await _original_fresh_top(symbol, timeout=timeout)
        except (RuntimeError, asyncio.TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < _ATTEMPTS:
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
    raise RuntimeError(
        f"no fresh Gemini BTC5m depth after {_ATTEMPTS} independent attempts"
    ) from last_error


async def run() -> None:
    # Patch only the market-data helper referenced by the inventory canary.
    inv.base.fresh_top = reliable_fresh_top
    await inv.run()


if __name__ == "__main__":
    asyncio.run(run())

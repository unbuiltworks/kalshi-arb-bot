"""Reusable fresh-only retry helper for Gemini BTC5 depth acquisition.

Preserves the proven August 27 behavior:
- retry independent fresh websocket subscriptions;
- bounded attempts and timeout;
- no REST fallback;
- no stale-price fallback;
- raise after the final failure so callers remain fail-closed.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def reliable_fresh_top(
    fresh_top: Callable[..., Awaitable[T]],
    symbol: str,
    *,
    attempts: int = 3,
    timeout_seconds: float = 4.0,
    retry_delay_seconds: float = 0.25,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds cannot be negative")

    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fresh_top(symbol, timeout=timeout_seconds)
        except (RuntimeError, asyncio.TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < attempts:
                await asyncio.sleep(retry_delay_seconds)

    raise RuntimeError(
        f"no fresh Gemini BTC5m depth after {attempts} independent attempts"
    ) from last_error

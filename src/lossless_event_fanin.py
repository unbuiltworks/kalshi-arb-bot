"""Lossless two-venue queue fan-in for the read-only hot scanners.

The original hot scanner waited on one ``Queue.get`` per venue and returned an
arbitrary member of the completed task set. If both venue tasks completed in the
same event-loop turn, one BookUpdate could be discarded. Cached books remained
correct, but a different market could lose its only evaluation trigger.

This helper preserves every completed update while keeping the existing one-update
``_next_update`` call signature. It is intentionally small and transport-agnostic.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Awaitable, Callable

_pending: deque[Any] = deque()
_store_key: tuple[int, int] | None = None


async def next_update_lossless(poly: Any, kalshi: Any) -> Any:
    """Return the next venue update without dropping a simultaneous completion."""
    global _store_key

    key = (id(poly), id(kalshi))
    if key != _store_key:
        # A reconnect/session replacement must never inherit a stale buffered event.
        _pending.clear()
        _store_key = key

    if _pending:
        return _pending.popleft()

    poly_task = asyncio.create_task(poly.updates.get())
    kalshi_task = asyncio.create_task(kalshi.updates.get())
    done, pending = await asyncio.wait(
        {poly_task, kalshi_task}, return_when=asyncio.FIRST_COMPLETED
    )

    # Preserve tasks that raced to completion after asyncio.wait returned but before
    # cancellation reached them. Only truly pending gets are cancelled.
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    completed = list(done)
    completed.extend(
        task
        for task in pending
        if task.done() and not task.cancelled() and task.exception() is None
    )
    updates = [task.result() for task in completed]
    if not updates:
        # Defensive only: FIRST_COMPLETED guarantees at least one result unless the
        # caller cancelled us, in which case CancelledError normally propagates.
        raise RuntimeError("hot event fan-in woke without an update")

    # Preserve receive order when both venues wake together. BookUpdate carries the
    # monotonic timestamp assigned when the store accepted the venue message.
    updates.sort(key=lambda update: getattr(update, "mono_ns", 0))
    _pending.extend(updates[1:])
    return updates[0]


def install(module: Any) -> None:
    """Patch a hot-scanner module's private fan-in without changing its callers."""
    module._next_update = next_update_lossless

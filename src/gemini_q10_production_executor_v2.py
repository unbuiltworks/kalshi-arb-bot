"""Hot-path refinement for the bounded Gemini q10 production executor.

V2 preserves all V1 risk/arming/state logic and changes only campaign plumbing:
- once a production candidate is selected, keep Gemini depth subscribed in memory;
- fail closed if that depth becomes stale while quotes are resting;
- serve all post-candidate fresh-top reads from the hot cache;
- emit decision->order-ack latency with spot/book ages at the decision point.

No new live permission is introduced. V1's dual-key arm and separate continuous-mode
flag remain authoritative.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import time
from typing import Any

from src import gemini_q10_production_executor as v1
from src.gemini_prediction_book_cache import GeminiTopCache


async def _run_campaign_hot(*, guard: Any, campaign_no: int) -> dict[str, Any]:
    original_wait_candidate = v1.probe._wait_candidate
    original_fresh_top = v1.base.fresh_top
    original_post = v1._post
    original_spot_decision = guard.decision

    cache: GeminiTopCache | None = None
    stop: asyncio.Event | None = None
    cache_task: asyncio.Task[Any] | None = None

    async def hot_wait_candidate():
        nonlocal cache, stop, cache_task
        found = await original_wait_candidate()
        event = found[0]
        cache = GeminiTopCache(event.symbol, stale_ms=750)
        stop = asyncio.Event()
        cache_task = asyncio.create_task(cache.run(stop))
        t0 = time.perf_counter_ns()
        snap = await cache.wait_fresh(timeout=3.0, max_age_ms=500)
        warm_ms = (time.perf_counter_ns() - t0) / 1_000_000
        v1.emit(
            "HOT_BOOK_READY",
            symbol=event.symbol,
            warmup_ms=round(warm_ms, 3),
            book_age_ms=round(snap.age_ms, 3),
            book_sequence=snap.sequence,
            order_entry=False,
        )
        return found

    async def hot_fresh_top(symbol: str, timeout: float = 4.0):
        if cache is None or cache.symbol != symbol:
            return await original_fresh_top(symbol, timeout=timeout)
        snap = await cache.wait_fresh(timeout=min(timeout, 2.0), max_age_ms=500)
        return snap.as_top()

    def composite_spot_decision():
        decision = original_spot_decision()
        if cache is None:
            return decision
        snap = cache.snapshot()
        if snap is None or snap.age_ms > cache.stale_ms:
            return replace(
                decision,
                ready=False,
                dangerous=True,
                reason="GEMINI_BOOK_STALE",
            )
        return decision

    async def hot_post(*args: Any, **kwargs: Any):
        spot = original_spot_decision()
        book = None if cache is None else cache.snapshot()
        t0 = time.perf_counter_ns()
        result = await original_post(*args, **kwargs)
        decision_to_ack_ms = (time.perf_counter_ns() - t0) / 1_000_000
        ack = result[0]
        v1.emit(
            "HOT_PATH_ORDER_ACK",
            client_order_id=kwargs.get("client_id"),
            outcome=kwargs.get("outcome"),
            ack_status=ack.get("status") if isinstance(ack, dict) else None,
            decision_to_ack_ms=round(decision_to_ack_ms, 3),
            transport_ack_ms=round(float(result[2]), 3),
            spot_age_at_decision_ms=None if spot.age_ms is None else round(float(spot.age_ms), 3),
            spot_move_bps=None if spot.move_bps is None else str(spot.move_bps),
            book_age_at_decision_ms=None if book is None else round(book.age_ms, 3),
            book_sequence=None if book is None else book.sequence,
            order_entry=True,
        )
        return result

    try:
        v1.probe._wait_candidate = hot_wait_candidate
        v1.base.fresh_top = hot_fresh_top
        v1._post = hot_post
        guard.decision = composite_spot_decision
        return await v1._run_campaign(guard=guard, campaign_no=campaign_no)
    finally:
        v1.probe._wait_candidate = original_wait_candidate
        v1.base.fresh_top = original_fresh_top
        v1._post = original_post
        guard.decision = original_spot_decision
        if stop is not None:
            stop.set()
        if cache_task is not None:
            cache_task.cancel()
            try:
                await cache_task
            except asyncio.CancelledError:
                pass


async def run() -> None:
    original = v1._run_campaign
    try:
        v1._run_campaign = _run_campaign_hot
        await v1.run()
    finally:
        v1._run_campaign = original


if __name__ == "__main__":
    asyncio.run(run())

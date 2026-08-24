"""Verified private-order transport wrapper for the structural q1 supervisor.

The first live maker still has to prove the authenticated private order stream.
Once the stream has demonstrably delivered real order updates, an intermittent
per-order miss no longer kills the whole lane: that order alone falls back to
paced exact REST reads while all existing cancel/reconciliation protections stay
in force. Restart cleanup deliberately uses a wider lookback than live discovery
so a late restart cannot forget a still-open maker from an older active event.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable, Mapping

from polymarket_us import PolymarketUS

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
import src.sports_poly_structural_live_supervisor as supervisor
from src.sports_poly_private_order_stream import PrivateOrderStream

REST_FALLBACK_GAP_SECONDS = 0.75
HORIZON_HOURS = int(os.getenv("SPORTS_STRUCTURAL_HORIZON_HOURS", "168"))
CLEANUP_LOOKBACK_HOURS = int(os.getenv("SPORTS_STRUCTURAL_CLEANUP_LOOKBACK_HOURS", "24"))


def install_verified_private_order_transport(stream: PrivateOrderStream) -> Callable[[], None]:
    original_refresh = v4._refresh_order
    original_cancel = v4._cancel_scoped
    force_rest_refresh: set[str] = set()
    rest_fallback_ids: set[str] = set()
    rest_cache: dict[str, Mapping[str, Any]] = {}
    last_rest_mono: dict[str, float] = {}

    async def paced_rest_refresh(
        client: PolymarketUS,
        oid: str,
        slug: str,
        fallback: Mapping[str, Any],
        *,
        force: bool = False,
    ) -> Mapping[str, Any]:
        now = time.monotonic()
        cached = rest_cache.get(oid, fallback)
        if not force and now - last_rest_mono.get(oid, 0.0) < REST_FALLBACK_GAP_SECONDS:
            return cached
        row = await original_refresh(client, oid, slug, cached)
        rest_cache[oid] = row
        last_rest_mono[oid] = time.monotonic()
        return row

    async def ws_refresh(
        client: PolymarketUS,
        oid: str,
        slug: str,
        fallback: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        stream.register_fallback(oid, fallback)
        if oid in force_rest_refresh:
            force_rest_refresh.discard(oid)
            return await paced_rest_refresh(client, oid, slug, fallback, force=True)
        if oid in rest_fallback_ids:
            return await paced_rest_refresh(client, oid, slug, fallback)
        if not stream.healthy:
            rest_fallback_ids.add(oid)
            return await paced_rest_refresh(client, oid, slug, fallback, force=True)

        if stream.seen(oid):
            cached = stream.get(oid)
            if cached is not None:
                return cached

        try:
            updated = await stream.wait_for_update(oid, supervisor.WS_REFRESH_WAIT_SECONDS)
        except RuntimeError:
            if stream.updates <= 0:
                raise
            rest_fallback_ids.add(oid)
            return await paced_rest_refresh(client, oid, slug, fallback, force=True)
        return updated if updated is not None else fallback

    async def ws_cancel(client: PolymarketUS, oid: str, slug: str) -> None:
        await original_cancel(client, oid, slug)
        if oid in rest_fallback_ids or not stream.healthy:
            force_rest_refresh.add(oid)
            return
        terminal = await stream.wait_terminal(oid, supervisor.WS_CANCEL_CONFIRM_SECONDS)
        if terminal is None:
            force_rest_refresh.add(oid)

    v4._refresh_order = ws_refresh
    v4._cancel_scoped = ws_cancel

    def restore() -> None:
        v4._refresh_order = original_refresh
        v4._cancel_scoped = original_cancel

    return restore


async def current_total_slugs() -> list[str]:
    feed, _, _ = await supervisor.discover_structural_pairs(
        horizon_hours=HORIZON_HOURS,
        lookback_hours=CLEANUP_LOOKBACK_HOURS,
    )
    return sorted({
        str(slug)
        for slug, market in feed.items()
        if (identity := supervisor.structural_identity(market)) is not None
        and str(identity[0]).startswith("TOTAL::")
    })


async def main() -> None:
    supervisor.install_private_order_transport = install_verified_private_order_transport
    supervisor._current_total_slugs = current_total_slugs
    await supervisor.main()


if __name__ == "__main__":
    asyncio.run(main())

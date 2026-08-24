"""Verified private-order transport wrapper for the structural q1 supervisor.

Private order WebSocket remains primary state transport with paced REST fallback.
The revenue loop shares one maker across structural totals and participation-gated
MLB props. Restart cleanup covers every family this process can mutate and batches
large MLB slug sets so authenticated order-list limits cannot block startup.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable, Mapping

from polymarket_us import PolymarketUS

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
import src.sports_poly_structural_live_supervisor as supervisor
import src.sports_poly_revenue_campaign_q1 as revenue_campaign
from src.sports_poly_direct_mlb_campaign_q1 import current_direct_mlb_slugs
from src.sports_poly_private_order_stream import PrivateOrderStream

REST_FALLBACK_GAP_SECONDS = 0.75
HORIZON_HOURS = int(os.getenv("SPORTS_STRUCTURAL_HORIZON_HOURS", "168"))
CLEANUP_LOOKBACK_HOURS = int(os.getenv("SPORTS_STRUCTURAL_CLEANUP_LOOKBACK_HOURS", "24"))
ORDER_LIST_BATCH_SIZE = max(5, min(50, int(os.getenv("SPORTS_POLY_CLEANUP_BATCH_SIZE", "25"))))
ORDER_LIST_BATCH_GAP_SECONDS = float(os.getenv("SPORTS_POLY_CLEANUP_BATCH_GAP_SECONDS", "0.12"))


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


async def current_revenue_slugs() -> list[str]:
    """Cleanup universe for every family this process can mutate."""
    feed, _, _ = await supervisor.discover_structural_pairs(
        horizon_hours=HORIZON_HOURS,
        lookback_hours=CLEANUP_LOOKBACK_HOURS,
    )
    total_slugs = {
        str(slug)
        for slug, market in feed.items()
        if (identity := supervisor.structural_identity(market)) is not None
        and str(identity[0]).startswith("TOTAL::")
    }
    mlb_slugs = set(await current_direct_mlb_slugs())
    return sorted(total_slugs | mlb_slugs)


async def _list_orders_batched(client: PolymarketUS, slugs: list[str]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for start in range(0, len(slugs), ORDER_LIST_BATCH_SIZE):
        batch = slugs[start : start + ORDER_LIST_BATCH_SIZE]
        response = await asyncio.to_thread(client.orders.list, {"slugs": batch})
        orders = response.get("orders") or [] if isinstance(response, Mapping) else []
        rows.extend(raw for raw in orders if isinstance(raw, Mapping))
        if start + ORDER_LIST_BATCH_SIZE < len(slugs):
            await asyncio.sleep(max(0.0, ORDER_LIST_BATCH_GAP_SECONDS))
    return rows


async def cleanup_current_revenue_orders_batched() -> dict[str, Any]:
    """Cancel/verify current revenue orders without oversized authenticated queries."""
    slugs = await current_revenue_slugs()
    if not slugs:
        return {
            "status": "CLEANUP_NO_REVENUE_SLUGS",
            "slugs": 0,
            "batches": 0,
            "open_seen": 0,
            "canceled": 0,
            "unresolved": 0,
            "partial_flattened": 0,
            "partial_unresolved": 0,
            "canceled_partial_audit_count": 0,
        }

    client = PolymarketUS(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        timeout=8.0,
    )
    try:
        rows = await _list_orders_batched(client, slugs)
        slug_set = set(slugs)
        canceled_partial_audit = [
            supervisor._order_summary(raw)
            for raw in rows
            if str(raw.get("marketSlug") or raw.get("market_slug") or "") in slug_set
            and str(raw.get("state") or "") == "ORDER_STATE_CANCELED"
            and supervisor._d(raw.get("cumQuantity") or raw.get("cum_quantity")) > 0
        ]
        open_orders = [
            raw
            for raw in rows
            if str(raw.get("id") or "")
            and str(raw.get("marketSlug") or raw.get("market_slug") or "") in slug_set
            and str(raw.get("state") or "") not in supervisor.TERMINAL_STATES
        ]
        canceled: list[str] = []
        unresolved: list[str] = []
        partial_flattened: list[str] = []
        partial_unresolved: list[str] = []

        for raw in open_orders:
            oid = str(raw.get("id") or "")
            slug = str(raw.get("marketSlug") or raw.get("market_slug") or "")
            cum_before = supervisor._d(raw.get("cumQuantity") or raw.get("cum_quantity"))
            try:
                await asyncio.to_thread(client.orders.cancel, oid, {"marketSlug": slug})
                canceled.append(oid)
            except Exception:
                unresolved.append(oid)
                continue

            if cum_before > 0:
                try:
                    await asyncio.to_thread(
                        client.orders.close_position,
                        {
                            "marketSlug": slug,
                            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
                            "synchronousExecution": True,
                            "maxBlockTime": "2",
                        },
                    )
                    partial_flattened.append(oid)
                except Exception:
                    partial_unresolved.append(oid)

        if open_orders:
            await asyncio.sleep(1.0)
            verify_rows = await _list_orders_batched(client, slugs)
            for raw in verify_rows:
                oid = str(raw.get("id") or "")
                slug = str(raw.get("marketSlug") or raw.get("market_slug") or "")
                state = str(raw.get("state") or "")
                if oid and slug in slug_set and state not in supervisor.TERMINAL_STATES:
                    unresolved.append(oid)

        return {
            "status": "STRUCTURAL_REVENUE_ORPHAN_CLEANUP",
            "slugs": len(slugs),
            "batches": (len(slugs) + ORDER_LIST_BATCH_SIZE - 1) // ORDER_LIST_BATCH_SIZE,
            "batch_size": ORDER_LIST_BATCH_SIZE,
            "open_seen": len(open_orders),
            "canceled": len(canceled),
            "unresolved": len(set(unresolved)),
            "partial_flattened": len(partial_flattened),
            "partial_unresolved": len(set(partial_unresolved)),
            "canceled_partial_audit_count": len(canceled_partial_audit),
            "canceled_partial_audit": canceled_partial_audit[-8:],
        }
    finally:
        client.close()


async def main() -> None:
    supervisor.install_private_order_transport = install_verified_private_order_transport
    supervisor._current_total_slugs = current_revenue_slugs
    supervisor.cleanup_current_total_orders = cleanup_current_revenue_orders_batched
    supervisor.run_campaign = revenue_campaign.run_once
    await supervisor.main()


if __name__ == "__main__":
    asyncio.run(main())

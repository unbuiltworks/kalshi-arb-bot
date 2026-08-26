"""Fast authoritative startup cleanup for the sports revenue executor.

Polymarket US orders.list() returns all open account orders when called without slug
filters. Read that set once, filter to the revenue universe client-side, cancel any
matching orphan, flatten known partial quantity, then verify with one more all-open
read only when a cancellation occurred. This replaces dozens of per-slug batches.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Mapping

from polymarket_us import PolymarketUS

import src.sports_poly_structural_live_supervisor as supervisor
import src.sports_poly_structural_live_supervisor_verified as verified

READ_TIMEOUT_S = 12.0


async def _all_open(client: PolymarketUS) -> list[Mapping[str, Any]]:
    response = await asyncio.wait_for(asyncio.to_thread(client.orders.list), timeout=READ_TIMEOUT_S)
    rows = response.get("orders") or [] if isinstance(response, Mapping) else []
    return [row for row in rows if isinstance(row, Mapping)]


async def cleanup_current_revenue_orders_fast() -> dict[str, Any]:
    slugs = await verified.current_revenue_slugs()
    slug_set = set(slugs)
    client = PolymarketUS(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        timeout=8.0,
    )
    try:
        rows = await _all_open(client)
        open_orders = [
            row for row in rows
            if str(row.get("id") or "")
            and str(row.get("marketSlug") or row.get("market_slug") or "") in slug_set
            and str(row.get("state") or "") not in supervisor.TERMINAL_STATES
        ]
        canceled: list[str] = []
        unresolved: list[str] = []
        partial_flattened: list[str] = []
        partial_unresolved: list[str] = []

        for row in open_orders:
            oid = str(row.get("id") or "")
            slug = str(row.get("marketSlug") or row.get("market_slug") or "")
            cum = supervisor._d(row.get("cumQuantity") or row.get("cum_quantity"))
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(client.orders.cancel, oid, {"marketSlug": slug}),
                    timeout=READ_TIMEOUT_S,
                )
                canceled.append(oid)
            except Exception:
                unresolved.append(oid)
                continue

            if cum > 0:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            client.orders.close_position,
                            {
                                "marketSlug": slug,
                                "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
                                "synchronousExecution": True,
                                "maxBlockTime": "2",
                            },
                        ),
                        timeout=READ_TIMEOUT_S,
                    )
                    partial_flattened.append(oid)
                except Exception:
                    partial_unresolved.append(oid)

        if open_orders:
            await asyncio.sleep(0.5)
            verify = await _all_open(client)
            for row in verify:
                oid = str(row.get("id") or "")
                slug = str(row.get("marketSlug") or row.get("market_slug") or "")
                state = str(row.get("state") or "")
                if oid and slug in slug_set and state not in supervisor.TERMINAL_STATES:
                    unresolved.append(oid)

        return {
            "status": "STRUCTURAL_REVENUE_ORPHAN_CLEANUP_FAST_ALL_OPEN",
            "revenue_slugs": len(slugs),
            "account_open_orders_seen": len(rows),
            "matching_open_seen": len(open_orders),
            "authenticated_list_calls": 1 + (1 if open_orders else 0),
            "canceled": len(canceled),
            "unresolved": len(set(unresolved)),
            "partial_flattened": len(partial_flattened),
            "partial_unresolved": len(set(partial_unresolved)),
        }
    finally:
        client.close()

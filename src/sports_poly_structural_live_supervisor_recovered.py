"""Recovered structural live supervisor after the 2026-08-24 MLB WS bootstrap halt.

Keeps the private order WebSocket as the primary resting-order transport, but a silent
or bootstrap-failing WS now degrades that exact order to bounded authoritative REST
instead of discarding known order identity via RuntimeError. Any REST ambiguity still
propagates and hard-halts. Before live order entry, the unresolved MLB reservation is
reconciled against open orders and authenticated portfolio trade activity.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Mapping

from polymarket_us import PolymarketUS

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
import src.sports_poly_revenue_campaign_q1_resilient as resilient_campaign
import src.sports_poly_structural_live_supervisor as supervisor
import src.sports_poly_structural_live_supervisor_verified as verified
from src.sports_poly_fast_orphan_cleanup import cleanup_current_revenue_orders_fast
from src.sports_poly_mlb_startup_recovery import recover_unresolved_mlb_reservation
from src.sports_poly_private_order_stream import PrivateOrderStream


def install_recovered_private_order_transport(stream: PrivateOrderStream) -> Callable[[], None]:
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
        if not force and now - last_rest_mono.get(oid, 0.0) < verified.REST_FALLBACK_GAP_SECONDS:
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
        except RuntimeError as exc:
            rest_fallback_ids.add(oid)
            print(
                "SPORTS_STRUCTURAL_SUPERVISOR "
                + json.dumps(
                    {
                        "status": "PRIVATE_ORDER_WS_BOOTSTRAP_DEGRADED_TO_REST",
                        "order_id": oid,
                        "market_slug": slug,
                        "error_type": type(exc).__name__,
                        "ws_updates": stream.updates,
                        "order_entry": False,
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
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


async def main() -> None:
    try:
        recovery = await recover_unresolved_mlb_reservation()
    except Exception as exc:
        print(
            "SPORTS_STRUCTURAL_SUPERVISOR "
            + json.dumps(
                {
                    "status": "MLB_STARTUP_RECOVERY_EXCEPTION_HARD_HALT",
                    "error_type": type(exc).__name__,
                    "order_entry": False,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        return

    print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps(recovery, separators=(",", ":"), default=str), flush=True)
    if not bool(recovery.get("clear")):
        print(
            "SPORTS_STRUCTURAL_SUPERVISOR "
            + json.dumps(
                {
                    "status": "MLB_UNRESOLVED_EXPOSURE_HARD_HALT",
                    "recovery_status": recovery.get("status"),
                    "trade_id": recovery.get("trade_id"),
                    "order_entry": False,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        return

    supervisor.install_private_order_transport = install_recovered_private_order_transport
    supervisor._current_total_slugs = verified.current_revenue_slugs
    supervisor.cleanup_current_total_orders = cleanup_current_revenue_orders_fast
    supervisor.run_campaign = resilient_campaign.run_once
    await supervisor.main()


if __name__ == "__main__":
    asyncio.run(main())

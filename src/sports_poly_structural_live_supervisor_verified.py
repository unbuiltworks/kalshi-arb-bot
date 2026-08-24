"""Verified private-order transport wrapper for the structural q1 supervisor.

This keeps the existing structural execution and restart-safety supervisor intact,
but fixes one bootstrap ambiguity: an order cached only from the create response
must never be mistaken for an order actually observed on the authenticated private
WebSocket.  The first live maker therefore has to be seen by the stream within the
bounded bootstrap window, or the existing campaign scoped-cancels and hard-halts.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping

from polymarket_us import PolymarketUS

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
import src.sports_poly_structural_live_supervisor as supervisor
from src.sports_poly_private_order_stream import PrivateOrderStream


def install_verified_private_order_transport(stream: PrivateOrderStream) -> Callable[[], None]:
    original_refresh = v4._refresh_order
    original_cancel = v4._cancel_scoped
    force_rest_refresh: set[str] = set()

    async def ws_refresh(
        client: PolymarketUS,
        oid: str,
        slug: str,
        fallback: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        stream.register_fallback(oid, fallback)
        if oid in force_rest_refresh:
            force_rest_refresh.discard(oid)
            return await original_refresh(client, oid, slug, fallback)
        if not stream.healthy:
            return await original_refresh(client, oid, slug, fallback)

        # Only stream.seen() proves that the private subscription actually
        # delivered this order.  A create-response fallback in the cache is not
        # sufficient proof and must flow through bounded bootstrap verification.
        if stream.seen(oid):
            cached = stream.get(oid)
            if cached is not None:
                return cached

        updated = await stream.wait_for_update(oid, supervisor.WS_REFRESH_WAIT_SECONDS)
        return updated if updated is not None else fallback

    async def ws_cancel(client: PolymarketUS, oid: str, slug: str) -> None:
        await original_cancel(client, oid, slug)
        if not stream.healthy:
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
    supervisor.install_private_order_transport = install_verified_private_order_transport
    await supervisor.main()


if __name__ == "__main__":
    asyncio.run(main())

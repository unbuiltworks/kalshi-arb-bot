"""Venue-specific hardening for the first bounded sports K/P live attempt.

These subclasses are the only executors the live coordinator should instantiate.
They add explicit exchange/market-state checks and use the documented Polymarket
US close-position payload for emergency recovery.  They do not create ARM state.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import time
from typing import Any

from src.sports_kp_execution import (
    ExecutionLeg,
    KalshiExecutor,
    ONE,
    PolymarketExecutor,
    VenueResult,
    _tick_aligned,
)

D = Decimal


class HardenedPolymarketExecutor(PolymarketExecutor):
    async def preflight(self, leg: ExecutionLeg) -> None:
        market = await asyncio.to_thread(self.client.markets.retrieve_by_slug, leg.market_id)
        if market.get("active") is False or market.get("closed") is True or market.get("archived") is True:
            raise PermissionError("Polymarket market is not active for trading")
        if market.get("orderBookEnabled") is False:
            raise PermissionError("Polymarket order book is disabled")
        min_qty = D(str(market.get("minimumTradeQty") or "1"))
        tick = D(str(market.get("orderPriceMinTickSize") or "0.01"))
        if min_qty > ONE or not _tick_aligned(ONE, min_qty):
            raise ValueError("Polymarket market does not accept exactly one contract")
        if not _tick_aligned(leg.yes_book_limit, tick):
            raise ValueError("Polymarket price is not aligned to current market tick")
        if await self.position(leg.market_id) != 0:
            raise PermissionError("pre-existing Polymarket position blocks bounded recovery")

    async def emergency_flatten(self, leg: ExecutionLeg) -> VenueResult:
        """Close the entire one-contract position with the documented close API."""
        try:
            body = await asyncio.to_thread(
                self.client.orders.close_position,
                {
                    "marketSlug": leg.market_id,
                    "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
                    "synchronousExecution": True,
                    "maxBlockTime": "2",
                    "slippageTolerance": {"ticks": 20},
                },
            )
            order_id = str(body.get("id") or "") or None
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                if await self.position(leg.market_id) == 0:
                    return VenueResult("polymarket", order_id, "FLATTENED", ONE, None, None, True)
                await asyncio.sleep(0.1)
            return VenueResult(
                "polymarket", order_id, "UNKNOWN", D("0"), None,
                "FLATTEN_POSITION_NOT_ZERO", True,
            )
        except Exception as exc:
            return VenueResult(
                "polymarket", None, "UNKNOWN", D("0"), None,
                f"FLATTEN_{type(exc).__name__}:{str(exc)[:160]}",
            )


class HardenedKalshiExecutor(KalshiExecutor):
    async def preflight(self, leg: ExecutionLeg) -> None:
        status_response = await self.client.get("/exchange/status")
        status_response.raise_for_status()
        status = status_response.json()
        if not bool(status.get("exchange_active")) or not bool(status.get("trading_active")):
            raise PermissionError("Kalshi exchange is not currently trading")

        market_response = await self.client.get(f"/markets/{leg.market_id}")
        market_response.raise_for_status()
        market = market_response.json().get("market") or {}
        market_status = str(market.get("status") or "").lower()
        if market_status not in {"open", "active"}:
            raise PermissionError(f"Kalshi market is not open: {market_status or 'unknown'}")

        if await self.position(leg.market_id) != 0:
            raise PermissionError("pre-existing Kalshi position blocks bounded recovery")

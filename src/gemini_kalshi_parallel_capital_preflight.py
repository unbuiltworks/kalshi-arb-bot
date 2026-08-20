"""Read-only live-account preflight for the parallel crypto capital allocator."""
from __future__ import annotations

import asyncio
import json

from src.gemini_kalshi_crypto_parallel_campaign import (
    ASSETS,
    CapitalBook,
    VenueCapitalBook,
    _live_venue_balances,
    D,
)


async def run() -> None:
    balances = await _live_venue_balances()
    global_book = CapitalBook(D("5"))
    venue_book = VenueCapitalBook(balances)
    authorized = []
    blocked = []
    for asset in ASSETS:
        key = f"preflight-{asset.lower()}"
        try:
            global_book.reserve(key, D("1"))
            try:
                venue_book.reserve_pair(key, D("1"))
            except Exception:
                global_book.release(key)
                raise
            authorized.append(asset)
        except Exception as exc:
            blocked.append({"asset": asset, "reason": f"{type(exc).__name__}: {str(exc)}"})
    report = {
        "mode": "READ_ONLY_PARALLEL_CAPITAL_PREFLIGHT",
        "assets_requested": list(ASSETS),
        "assets_authorized": authorized,
        "assets_blocked": blocked,
        "hard_pair_budget_usd": "5",
        "pair_reserved_usd": str(global_book.reserved),
        "gemini_available_usd": str(balances["gemini"]),
        "kalshi_available_usd": str(balances["kalshi"]),
        "gemini_reserved_usd": str(venue_book.reserved_for("gemini")),
        "kalshi_reserved_usd": str(venue_book.reserved_for("kalshi")),
        "gemini_free_after_reservations_usd": str(venue_book.free_for("gemini")),
        "kalshi_free_after_reservations_usd": str(venue_book.free_for("kalshi")),
        "all_four_lanes_capital_ready": len(authorized) == 4,
        "order_entry": False,
    }
    print("CRYPTO_PARALLEL_CAPITAL_PREFLIGHT " + json.dumps(report, separators=(",", ":"), sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(run())

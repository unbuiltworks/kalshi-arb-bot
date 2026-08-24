"""Guarded one-off release of a Command Center reservation after Gemini zero-fill proof.

The venue is re-read first. This module refuses to release unless the target client id
has no active order, zero aggregate filled quantity, and at least one terminal order.
It mutates only the ARB Command Center ledger, never Gemini or Kalshi.
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

from src import gemini_kalshi_crypto_maker_budget as budget
from src import gemini_xrp_orphan_reconcile_readonly as reconcile

TRADE_ID = os.getenv("GEMINI_ORPHAN_TRADE_ID", "").strip()
ARM = os.getenv("GEMINI_ORPHAN_RELEASE_ARM", "").strip()
KALSHI_TICKER = os.getenv("GEMINI_ORPHAN_KALSHI_TICKER", "").strip()


async def run() -> dict:
    if not TRADE_ID or ARM != TRADE_ID:
        raise RuntimeError("orphan release not explicitly armed for target trade id")
    if not KALSHI_TICKER:
        raise RuntimeError("GEMINI_ORPHAN_KALSHI_TICKER required")

    proof = await reconcile.run()
    if not bool(proof.get("safe_to_release_zero_fill_reservation")):
        raise RuntimeError("Gemini proof does not authorize zero-fill reservation release")

    pair = SimpleNamespace(
        asset="XRP",
        gemini_symbol=reconcile.SYMBOL,
        kalshi_ticker=KALSHI_TICKER,
    )
    await budget.release(
        pair=pair,
        trade_id=TRADE_ID,
        reason="AUTHORITATIVE_GEMINI_ZERO_FILL_ORPHAN_RECONCILIATION",
    )
    out = {
        "mode": "LEDGER_RECONCILIATION",
        "trade_id": TRADE_ID,
        "venue_mutation": False,
        "ledger_mutation": True,
        "status": "ZERO_FILL_RESERVATION_RELEASED",
        "total_filled_quantity": proof.get("total_filled_quantity"),
        "active_count": proof.get("active_count"),
        "unique_matching_orders": proof.get("unique_matching_orders"),
        "realized": False,
        "realized_pnl": "0",
    }
    print("GEMINI_XRP_ORPHAN_RELEASE " + json.dumps(out, separators=(",", ":")), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run())

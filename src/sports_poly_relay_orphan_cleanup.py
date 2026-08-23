"""Cancel only open orders on the bounded Polymarket MLB relay slug allowlist.

This is a startup safety repair for q1 maker probes. It never touches unrelated
Polymarket orders and never creates an order.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Mapping

from polymarket_us import PolymarketUS
from src.sports_kp_poly_relay_live_q1 import RELAY_PAIRS


async def run_once() -> dict[str, Any]:
    slugs = sorted({s for pair in RELAY_PAIRS for s in pair})
    client = PolymarketUS(key_id=os.environ["POLYMARKET_KEY_ID"], secret_key=os.environ["POLYMARKET_SECRET_KEY"], timeout=6.0)
    try:
        response = await asyncio.to_thread(client.orders.list, {"slugs": slugs})
        orders = response.get("orders") or [] if isinstance(response, Mapping) else []
        canceled: list[str] = []
        unresolved: list[str] = []
        for raw in orders:
            if not isinstance(raw, Mapping):
                continue
            oid = str(raw.get("id") or "")
            slug = str(raw.get("marketSlug") or "")
            state = str(raw.get("state") or "")
            if not oid or slug not in slugs:
                continue
            if state in {"ORDER_STATE_FILLED", "ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED", "ORDER_STATE_EXPIRED"}:
                continue
            try:
                await asyncio.to_thread(client.orders.cancel, oid, {"marketSlug": slug})
                canceled.append(oid)
            except Exception:
                unresolved.append(oid)
        out = {"status": "RELAY_ORPHAN_CLEANUP", "open_seen": len(orders), "canceled": len(canceled), "unresolved": len(unresolved), "order_entry": False}
        print("SPORTS_POLY_RELAY_ORPHAN_CLEANUP " + json.dumps(out, separators=(",", ":")), flush=True)
        return out
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run_once())

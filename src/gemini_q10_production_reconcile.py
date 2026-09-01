"""Read-only venue reconciliation for Gemini q10 production state.

No orders are created, cancelled or modified. If the durable production state is
open but Gemini reports zero short-crypto orders and positions, this utility marks
the local state reconciled and records the account balance observed at that time.
Trading P&L, maker rebates and liquidity rewards remain separate accounting fields.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json

from src import gemini_q10_production_executor as prod


async def run() -> None:
    state = prod._read_state()
    snap = await prod._flat_snapshot()
    available = D(str(await prod.base._gemini_available_usd()))
    out = {
        "classification": "READ_ONLY_Q10_PRODUCTION_RECONCILIATION_NOT_ORDER_ENTRY",
        "order_entry": False,
        "available_usd": str(available),
        "flat": not snap["orders"] and not snap["positions"],
        "exposure": snap,
        "state_present": state is not None,
        "state_open": bool((state or {}).get("open")),
        "local_state_updated": False,
        "realized_trade_pnl": None,
        "maker_rebates_realized": None,
        "liquidity_rewards_realized": None,
    }
    if state and state.get("open") and out["flat"]:
        before_raw = state.get("available_usd_before")
        delta = None
        if before_raw is not None:
            try:
                delta = available - D(str(before_raw))
            except Exception:
                delta = None
        state.update({
            "open": False,
            "status": "RECONCILED_ACCOUNT_FLAT",
            "available_usd_after_reconcile": str(available),
            "account_delta_at_reconcile": None if delta is None else str(delta),
            "reconciled_at_utc": datetime.now(timezone.utc).isoformat(),
            "realized_trade_pnl": None,
            "maker_rebates_realized": None,
            "liquidity_rewards_realized": None,
        })
        prod._write_state(state)
        out["local_state_updated"] = True
        out["account_delta_at_reconcile"] = None if delta is None else str(delta)
    print("GEMINI_Q10_PRODUCTION_RECONCILE " + json.dumps(out, separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(run())

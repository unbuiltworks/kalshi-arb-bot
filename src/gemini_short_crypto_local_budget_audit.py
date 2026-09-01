"""Read-only audit for the AWS-local Gemini short-crypto budget fallback."""
from __future__ import annotations

import asyncio
import json

from src import gemini_short_crypto_local_budget as local_budget


async def run() -> None:
    local = local_budget.local_open_state()
    snapshot = await local_budget.account_exposure_snapshot()
    print(json.dumps({
        "type": "GEMINI_SHORT_CRYPTO_LOCAL_BUDGET_AUDIT",
        "local_open_reservation": bool(local),
        "local_trade_id": None if local is None else local.get("trade_id"),
        **snapshot,
        "order_entry": False,
        "mutation": False,
        "realized_pnl": 0,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(run())

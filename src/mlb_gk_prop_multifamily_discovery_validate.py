"""Read-only live inventory validation for the isolated HR/TB/K canary."""
from __future__ import annotations

import asyncio
from collections import Counter
import json

import httpx

from src import mlb_gk_prop_live_multifamily_rule_risk_canary as canary
from src.gemini_kalshi_mlb_prop_hot_shadow import discover_hot


async def main() -> None:
    canary._install_home_run_discovery()
    async with httpx.AsyncClient(timeout=30.0) as http:
        hot, _market_to_keys, diagnostics = await discover_hot(http)
    selected = {
        key: row for key, row in hot.items()
        if row.family in canary.SUPPORTED_FAMILIES
        and row.gemini_side.side in {"YES", "CONTRACT"}
        and row.kalshi_side.side in {"YES", "CONTRACT"}
    }
    by_family = Counter(row.family for row in selected.values())
    print(json.dumps({
        "status": "MLB_GK_MULTIFAMILY_DISCOVERY_VALIDATE",
        "selected_hot_keys": len(selected),
        "selected_by_family": dict(by_family),
        "all_hot_keys": len(hot),
        "diagnostics": diagnostics,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

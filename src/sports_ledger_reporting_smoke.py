"""One-shot protected ARB Command Center ingest smoke test.

Writes only a SHADOW system-health event. It cannot affect realized P&L because the
Command Center only accepts P&L fields from TRADE_REALIZED events.
"""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import json
import os

import httpx


async def main() -> None:
    base = os.getenv("ARB_COMMAND_CENTER_URL", "https://arb-command-center-production.up.railway.app").rstrip("/")
    token = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
    if not token:
        raise RuntimeError("LEDGER_INGEST_TOKEN missing")
    ts = datetime.now(timezone.utc)
    event = {
        "event_id": f"sports-reporting-smoke-{ts.strftime('%Y%m%dT%H%M%SZ')}",
        "trade_id": "sports-reporting-system-health",
        "event_type": "REPORTING_SMOKE",
        "event_ts": ts.isoformat(),
        "strategy": "sports_system_health",
        "mode": "SHADOW",
        "market": "system_health",
        "asset": "sports",
        "venues": [],
        "side": "NONE",
        "status": "OK",
        "legs_required": 0,
        "legs_filled": 0,
        "metadata": {"purpose": "protected ledger ingest validation", "realized_pnl": False},
    }
    async with httpx.AsyncClient(timeout=5.0) as http:
        response = await http.post(
            f"{base}/api/events",
            headers={"Authorization": f"Bearer {token}"},
            json=event,
        )
        response.raise_for_status()
        print(json.dumps({
            "status": "SPORTS_LEDGER_REPORTING_SMOKE_OK",
            "http_status": response.status_code,
            "event_id": event["event_id"],
            "mode": "SHADOW",
            "event_type": "REPORTING_SMOKE",
            "realized_pnl_written": False,
        }, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

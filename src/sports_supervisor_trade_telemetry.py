"""Best-effort sports supervisor telemetry into the shared trade_events ledger.

Diagnostic-only. Failure to write must never alter scanning, candidate ranking, or
live execution semantics. These rows are deliberately not TRADE_REALIZED events and
carry no P&L.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Mapping
from uuid import uuid4

import psycopg

EVENT_TYPE = "SPORTS_SUPERVISOR_TELEMETRY"
STRATEGY = "sports_kp_combo_supervisor"


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def _diag(payload: Mapping[str, Any]) -> None:
    """Emit non-secret telemetry health only; never log the connection string."""
    print(
        "SPORTS_SUPERVISOR_TELEMETRY_WRITE "
        + json.dumps(dict(payload), separators=(",", ":"), default=str),
        flush=True,
    )


async def emit_cycle(metadata: Mapping[str, Any]) -> bool:
    """Write one non-P&L manufacturing-cycle heartbeat, returning False on failure."""
    url = _database_url()
    if not url:
        _diag({"configured": False, "ok": False, "reason": "DATABASE_URL_MISSING"})
        return False
    now = datetime.now(timezone.utc)
    event_id = f"sports-telemetry-{uuid4().hex}"
    trade_id = f"sports-supervisor-{now.strftime('%Y%m%dT%H%M%SZ')}"
    payload = {
        "diagnostic_only": True,
        "count_as_realized": False,
        "order_entry": False,
        **dict(metadata),
    }
    try:
        async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
            await conn.execute(
                """
                INSERT INTO trade_events (
                    event_id, trade_id, event_type, event_ts, strategy, mode,
                    market, asset, venues, status, fee, metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    event_id,
                    trade_id,
                    EVENT_TYPE,
                    now,
                    STRATEGY,
                    "DIAGNOSTIC",
                    "SPORTS_MVE_RFQ",
                    "SPORTS",
                    ["kalshi", "polymarket"],
                    "HEARTBEAT",
                    0,
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
        _diag({"configured": True, "ok": True, "event_type": EVENT_TYPE})
        return True
    except Exception as exc:
        # Deliberately omit str(exc): connection errors can contain endpoint details.
        _diag(
            {
                "configured": True,
                "ok": False,
                "error_type": type(exc).__name__,
                "sqlstate": getattr(exc, "sqlstate", None),
            }
        )
        return False

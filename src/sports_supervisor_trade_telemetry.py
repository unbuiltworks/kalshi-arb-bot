"""Best-effort sports supervisor telemetry into the authoritative ARB ledger.

Diagnostic-only. Failure to write must never alter scanning, candidate ranking, or
live execution semantics. These rows are SHADOW telemetry, never TRADE_REALIZED,
and carry no P&L.

Telemetry uses the command-center /api/events ingest contract rather than a local
DATABASE_URL. This prevents a Railway service-local Postgres connection from
silently creating a second, non-authoritative ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Mapping
from uuid import uuid4

import httpx

EVENT_TYPE = "SPORTS_SUPERVISOR_TELEMETRY"
STRATEGY = "sports_kp_combo_supervisor"


def _ingest_url() -> str:
    return os.getenv("ARB_LEDGER_INGEST_URL", "").strip()


def _ingest_token() -> str:
    return os.getenv("LEDGER_INGEST_TOKEN", "").strip()


def _diag(payload: Mapping[str, Any]) -> None:
    """Emit non-secret telemetry health only; never log tokens or connection data."""
    print(
        "SPORTS_SUPERVISOR_TELEMETRY_WRITE "
        + json.dumps(dict(payload), separators=(",", ":"), default=str),
        flush=True,
    )


async def emit_cycle(metadata: Mapping[str, Any]) -> bool:
    """Write one non-P&L manufacturing-cycle heartbeat, returning False on failure."""
    url = _ingest_url()
    token = _ingest_token()
    if not url or not token:
        _diag({
            "configured": False,
            "ok": False,
            "reason": "LEDGER_INGEST_CONFIG_MISSING",
            "url_configured": bool(url),
            "token_configured": bool(token),
        })
        return False

    now = datetime.now(timezone.utc)
    event_id = f"sports-telemetry-{uuid4().hex}"
    trade_id = f"sports-supervisor-{now.strftime('%Y%m%dT%H%M%SZ')}"
    diagnostic_metadata = {
        "diagnostic_only": True,
        "count_as_realized": False,
        "order_entry": False,
        **dict(metadata),
    }
    event = {
        "event_id": event_id,
        "trade_id": trade_id,
        "event_type": EVENT_TYPE,
        "event_ts": now.isoformat(),
        "strategy": STRATEGY,
        "mode": "SHADOW",
        "market": "SPORTS_MVE_RFQ",
        "asset": "SPORTS",
        "venues": ["kalshi", "polymarket"],
        "status": "HEARTBEAT",
        "fee": 0,
        "metadata": diagnostic_metadata,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=event,
            )
        if response.status_code != 200:
            _diag({
                "configured": True,
                "ok": False,
                "transport": "COMMAND_CENTER_INGEST",
                "http_status": response.status_code,
            })
            return False
        try:
            body = response.json()
        except Exception:
            body = {}
        ok = bool(isinstance(body, Mapping) and body.get("ok"))
        _diag({
            "configured": True,
            "ok": ok,
            "transport": "COMMAND_CENTER_INGEST",
            "http_status": response.status_code,
            "duplicate": bool(body.get("duplicate")) if isinstance(body, Mapping) else False,
            "event_type": EVENT_TYPE,
        })
        return ok
    except Exception as exc:
        _diag({
            "configured": True,
            "ok": False,
            "transport": "COMMAND_CENTER_INGEST",
            "error_type": type(exc).__name__,
        })
        return False

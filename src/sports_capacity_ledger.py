"""Best-effort Command Center ingestion for read-only sports capacity observations.

Capacity telemetry must never alter or block live trading. This client therefore:
- emits only SHADOW `CAPACITY_OBSERVATION` events;
- never reports gross/net realized P&L;
- never raises on network/auth/dashboard failure;
- uses stable `capacity_key` metadata so Command Center can dedupe repeated scans;
- caps one epoch's emitted rows to avoid telemetry load becoming an execution issue.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import logging
import os
from typing import Any, Iterable

import httpx

D = Decimal
logger = logging.getLogger("kalshi_arb.sports_capacity_ledger")
MAX_ROWS_PER_EPOCH = 25


def _endpoint() -> tuple[str, str] | None:
    base = os.getenv("ARB_COMMAND_CENTER_URL", "").strip().rstrip("/")
    token = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
    if not base or not token:
        return None
    return f"{base}/api/events", token


def _stable_key(row: dict[str, Any]) -> str:
    return f"gk|{row.get('settlement_key') or ''}|{row.get('direction') or ''}"


def _event_payload(row: dict[str, Any], *, run_id: str, ordinal: int, now: datetime) -> dict[str, Any]:
    capacity_key = _stable_key(row)
    digest = hashlib.sha1(capacity_key.encode("utf-8")).hexdigest()[:14]
    quantity = int(row.get("largest_qualifying_tier") or 0)
    profit = D(str(row.get("largest_qualifying_locked_profit") or "0"))
    legs = row.get("legs") or []
    venues = [str(leg.get("venue")) for leg in legs if leg.get("venue")]
    market = str(row.get("settlement_key") or "")
    return {
        "event_id": f"cap:{run_id}:{ordinal}:{digest}",
        "trade_id": f"capacity:{run_id}:{digest}",
        "event_type": "CAPACITY_OBSERVATION",
        "event_ts": now.isoformat(),
        "strategy": "sports_gk_capacity_shadow",
        "mode": "SHADOW",
        "market": market,
        "venues": venues or ["gemini", "kalshi"],
        "side": str(row.get("direction") or ""),
        "status": "SHADOW_CAPACITY",
        "quantity": str(quantity),
        "opportunity_net_profit": str(profit),
        "metadata": {
            "capacity_key": capacity_key,
            "source_run_id": run_id,
            "largest_qualifying_tier": quantity,
            "largest_qualifying_locked_profit": str(profit),
            "largest_economic_tier": int(row.get("largest_economic_tier") or quantity),
            "largest_fundable_tier": int(row.get("largest_fundable_tier") or 0),
            "balances_supplied": bool(row.get("balances_supplied", False)),
            "max_top_level_depth": row.get("max_top_level_depth"),
            "min_profit_per_contract": row.get("min_profit_per_contract"),
            "read_only": True,
            "execution_eligible": False,
            "classification": "READ_ONLY_NON_ADDITIVE_CAPACITY_ESTIMATE",
        },
    }


async def emit_capacity_rows(rows: Iterable[dict[str, Any]], *, run_id: str) -> dict[str, int]:
    """Emit positive capacity rows without ever propagating an error to execution."""
    endpoint = _endpoint()
    if endpoint is None:
        return {"attempted": 0, "accepted": 0, "failed": 0, "skipped_no_config": 1}
    url, token = endpoint
    positive = [
        row for row in rows
        if int(row.get("largest_qualifying_tier") or 0) > 0
        and D(str(row.get("largest_qualifying_locked_profit") or "0")) > 0
    ]
    positive.sort(
        key=lambda row: D(str(row.get("largest_qualifying_locked_profit") or "0")),
        reverse=True,
    )
    positive = positive[:MAX_ROWS_PER_EPOCH]
    if not positive:
        return {"attempted": 0, "accepted": 0, "failed": 0, "skipped_no_config": 0}

    now = datetime.now(timezone.utc)
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(2.0, connect=1.0)

    async def one(index: int, row: dict[str, Any], http: httpx.AsyncClient) -> bool:
        try:
            response = await http.post(
                url,
                headers=headers,
                json=_event_payload(row, run_id=run_id, ordinal=index, now=now),
            )
            return 200 <= response.status_code < 300
        except Exception:
            return False

    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            results = await asyncio.gather(*(one(i, row, http) for i, row in enumerate(positive)))
        accepted = sum(bool(x) for x in results)
        return {
            "attempted": len(results),
            "accepted": accepted,
            "failed": len(results) - accepted,
            "skipped_no_config": 0,
        }
    except Exception as exc:
        logger.warning("SPORTS_CAPACITY_LEDGER_TELEMETRY_FAILED type=%s", type(exc).__name__)
        return {"attempted": len(positive), "accepted": 0, "failed": len(positive), "skipped_no_config": 0}

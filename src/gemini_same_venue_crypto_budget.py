"""Durable exposure/accounting gate for same-Gemini q1 crypto ladder arbitrage.

The execution lane only admits monotone HI threshold siblings from the exact same
Gemini event with identical settlement source + expiry. A completed YES(low) +
NO(high) pair has a deterministic >=$1 payout floor before fees, but no P&L is
reported as realized until settlement reconciliation.

This module serializes LIVE crypto exposure through Command Center and reserves
$1 before any maker mutation. Zero-fill attempts release the reservation. Any fill
or ambiguous mutation retains it until explicit execution/settlement reconciliation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os
from typing import Any, Mapping

import httpx

D = Decimal
COMMAND_CENTER = os.getenv(
    "ARB_COMMAND_CENTER_URL",
    "https://arb-command-center-production.up.railway.app",
).rstrip("/")
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
STRATEGY = "crypto_gemini_ladder_maker_q1"
CLASSIFICATION = "SAME_GEMINI_EVENT_MONOTONE_HI_LADDER_STRUCTURAL_ARBITRAGE"
TERMINAL = {
    "REALIZED", "TRADE_REALIZED", "SETTLED", "SETTLED_COMPATIBLE",
    "CAPITAL_RELEASED", "NO_FILL_RELEASED",
}


def _d(value: Any) -> D:
    if isinstance(value, Mapping):
        value = value.get("value")
    try:
        return D(str(value or "0"))
    except Exception:
        return D("0")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


async def trades() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, list):
        raise RuntimeError("command center trades response is not a list")
    return [row for row in body if isinstance(row, dict)]


def _is_live_crypto(row: Mapping[str, Any]) -> bool:
    mode = str(row.get("mode") or "").upper()
    strategy = str(row.get("strategy") or "").lower()
    return mode in {"LIVE", "LIVE_PROBE"} and strategy.startswith("crypto_")


def unresolved_crypto(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        if not _is_live_crypto(row):
            continue
        status = str(row.get("status") or "").upper()
        if status in TERMINAL:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        reserved = _d(metadata.get("reserved_usd"))
        filled = _d(row.get("capital_filled") or row.get("capital_wagered"))
        legs_filled = _d(row.get("legs_filled"))
        if reserved > 0 or filled > 0 or legs_filled > 0:
            return row
    return None


async def _post(event: dict[str, Any]) -> None:
    if not LEDGER_TOKEN:
        raise RuntimeError("LEDGER_INGEST_TOKEN missing; same-Gemini live mutation denied")
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.post(
            f"{COMMAND_CENTER}/api/events",
            headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
            json=event,
        )
        response.raise_for_status()


async def reserve(*, candidate: Mapping[str, Any], trade_id: str) -> None:
    rows = await trades()
    blocked = unresolved_crypto(rows)
    if blocked is not None:
        raise RuntimeError(f"unresolved LIVE crypto exposure blocks maker: {blocked.get('trade_id')}")
    market = f"{candidate['maker_symbol']}|{candidate['hedge_symbol']}"
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-reserved"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RESERVED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": market,
        "asset": str(candidate.get("event_ticker") or "CRYPTO"),
        "venues": ["gemini"],
        "side": "SAME_EVENT_MAKER_THEN_FOK_HEDGE",
        "status": "CAPITAL_RESERVED",
        "amount": "1",
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {
            "reserved_usd": "1",
            "q": 1,
            "classification": CLASSIFICATION,
            "guaranteed_payout_floor_before_fees": "1",
            "same_event": True,
            "same_source": True,
            "same_expiry": True,
            "source": candidate.get("source"),
            "expiry": candidate.get("expiry"),
            "maker_outcome": candidate.get("maker_outcome"),
            "hedge_outcome": candidate.get("hedge_outcome"),
            "maker_quote": candidate.get("maker_quote"),
            "hedge_limit_reserved": candidate.get("hedge_limit"),
            "modeled_locked_profit": candidate.get("locked_profit"),
            "realized": False,
        },
    })


async def release(*, candidate: Mapping[str, Any], trade_id: str, reason: str) -> None:
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-released"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RELEASED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": f"{candidate['maker_symbol']}|{candidate['hedge_symbol']}",
        "asset": str(candidate.get("event_ticker") or "CRYPTO"),
        "venues": ["gemini"],
        "side": "SAME_EVENT_MAKER_THEN_FOK_HEDGE",
        "status": "CAPITAL_RELEASED",
        "amount": "0",
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {
            "reserved_usd": "0",
            "classification": CLASSIFICATION,
            "release_reason": reason,
            "realized": False,
        },
    })


async def execution_result(*, candidate: Mapping[str, Any], trade_id: str, status: str,
                           legs_filled: int, metadata: Mapping[str, Any]) -> None:
    await _post({
        "event_id": _safe_id(f"{trade_id}:execution-result:{status}"),
        "trade_id": trade_id,
        "event_type": "EXECUTION_RESULT",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": f"{candidate['maker_symbol']}|{candidate['hedge_symbol']}",
        "asset": str(candidate.get("event_ticker") or "CRYPTO"),
        "venues": ["gemini"],
        "side": "SAME_EVENT_MAKER_THEN_FOK_HEDGE",
        "status": status,
        "quantity": "1",
        "legs_required": 2,
        "legs_filled": int(legs_filled),
        "metadata": {
            "classification": CLASSIFICATION,
            "guaranteed_payout_floor_before_fees": "1",
            "same_event": True,
            "same_source": True,
            "same_expiry": True,
            "realized": False,
            **dict(metadata),
        },
    })

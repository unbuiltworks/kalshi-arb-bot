"""Durable accounting gate for the isolated Gemini short-crypto q=1 canary.

This lane is NOT classified as structural arbitrage. It is a live execution/queue
validation of a two-sided maker hypothesis on one Gemini BTC 5-minute binary.
Nothing is realized until authoritative settlement/reconciliation.
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
STRATEGY = "crypto_gemini_btc5m_two_sided_maker_q1"
CLASSIFICATION = "GEMINI_SHORT_BINARY_TWO_SIDED_MAKER_Q1_CANARY"
RESERVED_USD = D("2")
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


async def _trades() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, list):
        raise RuntimeError("command center trades response is not a list")
    return [row for row in body if isinstance(row, dict)]


def _unresolved_live_crypto(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        mode = str(row.get("mode") or "").upper()
        strategy = str(row.get("strategy") or "").lower()
        if mode not in {"LIVE", "LIVE_PROBE"} or not strategy.startswith("crypto_"):
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
        raise RuntimeError("LEDGER_INGEST_TOKEN missing; Gemini short-crypto mutation denied")
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.post(
            f"{COMMAND_CENTER}/api/events",
            headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
            json=event,
        )
        response.raise_for_status()


async def reserve(*, symbol: str, event_ticker: str, trade_id: str,
                  yes_quote: str, no_quote: str, modeled_pair_edge: str) -> None:
    rows = await _trades()
    blocked = _unresolved_live_crypto(rows)
    if blocked is not None:
        raise RuntimeError(f"unresolved LIVE crypto exposure blocks canary: {blocked.get('trade_id')}")
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-reserved"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RESERVED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": symbol,
        "asset": event_ticker,
        "venues": ["gemini"],
        "side": "TWO_SIDED_MAKER",
        "status": "CAPITAL_RESERVED",
        "amount": str(RESERVED_USD),
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {
            "reserved_usd": str(RESERVED_USD),
            "q": 1,
            "classification": CLASSIFICATION,
            "event_ticker": event_ticker,
            "yes_quote": yes_quote,
            "no_quote": no_quote,
            "modeled_pair_edge": modeled_pair_edge,
            "reference_wallet_strategy_transfer_test": True,
            "guaranteed_profit": False,
            "realized": False,
        },
    })


async def release(*, symbol: str, event_ticker: str, trade_id: str, reason: str) -> None:
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-released:{reason}"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RELEASED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": symbol,
        "asset": event_ticker,
        "venues": ["gemini"],
        "side": "TWO_SIDED_MAKER",
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


async def result(*, symbol: str, event_ticker: str, trade_id: str,
                 status: str, legs_filled: int, metadata: Mapping[str, Any]) -> None:
    await _post({
        "event_id": _safe_id(f"{trade_id}:execution-result:{status}"),
        "trade_id": trade_id,
        "event_type": "EXECUTION_RESULT",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": symbol,
        "asset": event_ticker,
        "venues": ["gemini"],
        "side": "TWO_SIDED_MAKER",
        "status": status,
        "quantity": "1",
        "legs_required": 2,
        "legs_filled": int(legs_filled),
        "metadata": {
            "classification": CLASSIFICATION,
            "guaranteed_profit": False,
            "realized": False,
            **dict(metadata),
        },
    })

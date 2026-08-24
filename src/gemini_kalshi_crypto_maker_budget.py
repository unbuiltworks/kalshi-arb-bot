"""Durable shared exposure gate for Gemini/Kalshi 15m maker q1.

This lane is threshold-dominance INDEX/RULE RISK, not guaranteed arbitrage. Before
any maker mutation it must prove Command Center is reachable, no other unresolved
LIVE crypto exposure is present, and the existing $5 maximum realized-loss research
ceiling has not been exhausted. Exactly $1 is reserved before mutation. Confirmed
zero-fill outcomes release the reservation; fills/ambiguity retain it until explicit
reconciliation so another crypto service cannot overlap exposure.
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
STRATEGY = "crypto_gk_15m_maker_q1"
MAX_REALIZED_LOSS_BUDGET = D("5")  # Existing research ceiling. Never auto-increase/reset.
TERMINAL = {
    "REALIZED", "TRADE_REALIZED", "SETTLED", "SETTLED_COMPATIBLE",
    "CAPITAL_RELEASED", "NO_FILL_RELEASED",
}
CLASSIFICATION = "LIVE_THRESHOLD_DOMINANCE_INDEX_RISK_NOT_GUARANTEED_ARBITRAGE"


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


def _is_live_crypto(row: Mapping[str, Any]) -> bool:
    mode = str(row.get("mode") or "").upper()
    strategy = str(row.get("strategy") or "").lower()
    return mode in {"LIVE", "LIVE_PROBE"} and strategy.startswith("crypto_")


def _unresolved_crypto(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
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


def _risk_loss_used(rows: list[dict[str, Any]]) -> D:
    total = D("0")
    seen: set[str] = set()
    for row in rows:
        if not _is_live_crypto(row):
            continue
        trade_id = str(row.get("trade_id") or "")
        if trade_id and trade_id in seen:
            continue
        status = str(row.get("status") or "").upper()
        if status not in TERMINAL:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        classification = str(metadata.get("classification") or row.get("classification") or "").upper()
        if "RISK" not in classification:
            continue
        net = _d(row.get("net_pnl") or row.get("pnl_net") or row.get("realized_pnl"))
        if net < 0:
            total += -net
        if trade_id:
            seen.add(trade_id)
    return total


async def _post(event: dict[str, Any]) -> None:
    if not LEDGER_TOKEN:
        raise RuntimeError("LEDGER_INGEST_TOKEN missing; crypto maker live execution denied")
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.post(
            f"{COMMAND_CENTER}/api/events",
            headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
            json=event,
        )
        response.raise_for_status()


async def reserve(*, candidate: Mapping[str, Any], pair: Any, geometry: Mapping[str, Any], trade_id: str) -> dict[str, Any]:
    rows = await _trades()
    blocked = _unresolved_crypto(rows)
    if blocked is not None:
        raise RuntimeError(f"unresolved LIVE crypto exposure blocks maker: {blocked.get('trade_id')}")
    loss_used = _risk_loss_used(rows)
    if loss_used >= MAX_REALIZED_LOSS_BUDGET:
        raise RuntimeError(
            f"crypto risk realized-loss budget exhausted: used={loss_used} ceiling={MAX_REALIZED_LOSS_BUDGET}"
        )
    market = f"{pair.gemini_symbol}|{pair.kalshi_ticker}"
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-reserved"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RESERVED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": market,
        "asset": str(pair.asset),
        "venues": ["gemini", "kalshi"],
        "side": "GEMINI_MAKER_KALSHI_FOK_HEDGE",
        "status": "CAPITAL_RESERVED",
        "amount": "1",
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {
            "reserved_usd": "1",
            "q": 1,
            "classification": CLASSIFICATION,
            "guaranteed_arbitrage": False,
            "max_realized_loss_budget_usd": str(MAX_REALIZED_LOSS_BUDGET),
            "realized_risk_loss_used_usd": str(loss_used),
            "dominance_safe_direction": geometry.get("dominance_safe_direction"),
            "threshold_gap_bps": geometry.get("threshold_gap_bps"),
            "gemini_maker_outcome": candidate.get("maker_outcome"),
            "kalshi_hedge_outcome": candidate.get("hedge_outcome"),
            "modeled_locked_profit": str(candidate.get("locked_profit") or "0"),
        },
    })
    return {"reserved": True, "trade_id": trade_id, "loss_used": str(loss_used), "loss_ceiling": str(MAX_REALIZED_LOSS_BUDGET)}


async def release(*, pair: Any, trade_id: str, reason: str) -> None:
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-released"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RELEASED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": f"{pair.gemini_symbol}|{pair.kalshi_ticker}",
        "asset": str(pair.asset),
        "venues": ["gemini", "kalshi"],
        "side": "GEMINI_MAKER_KALSHI_FOK_HEDGE",
        "status": "CAPITAL_RELEASED",
        "amount": "0",
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {
            "reserved_usd": "0",
            "q": 1,
            "classification": CLASSIFICATION,
            "guaranteed_arbitrage": False,
            "release_reason": reason,
        },
    })


async def execution_result(*, pair: Any, trade_id: str, status: str, legs_filled: int, metadata: Mapping[str, Any]) -> None:
    await _post({
        "event_id": _safe_id(f"{trade_id}:execution-result:{status}"),
        "trade_id": trade_id,
        "event_type": "EXECUTION_RESULT",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": f"{pair.gemini_symbol}|{pair.kalshi_ticker}",
        "asset": str(pair.asset),
        "venues": ["gemini", "kalshi"],
        "side": "GEMINI_MAKER_KALSHI_FOK_HEDGE",
        "status": status,
        "quantity": "1",
        "legs_required": 2,
        "legs_filled": int(legs_filled),
        "metadata": {"classification": CLASSIFICATION, "guaranteed_arbitrage": False, **dict(metadata)},
    })

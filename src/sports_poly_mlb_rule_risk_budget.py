"""Durable shared exposure gate for the Polymarket MLB maker q1 canary.

This lane is rule-risk, not guaranteed arbitrage. Before any maker mutation it must
prove the Command Center is reachable, no other unresolved LIVE sports exposure is
present, and the existing maximum realized-loss research budget has not been used up.
It then durably reserves exactly $1. Confirmed zero-fill outcomes explicitly release
the reservation. Filled/ambiguous outcomes intentionally retain it until settlement
or manual reconciliation, preventing another service from overlapping exposure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os
from typing import Any, Mapping

import httpx

D = Decimal
ONE = D("1")
COMMAND_CENTER = os.getenv(
    "ARB_COMMAND_CENTER_URL",
    "https://arb-command-center-production.up.railway.app",
).rstrip("/")
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
STRATEGY = "sports_poly_mlb_maker_q1"
MAX_REALIZED_LOSS_BUDGET = D("5")  # Existing approved research-loss ceiling; never auto-increase.
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


def _is_live_sports(row: Mapping[str, Any]) -> bool:
    mode = str(row.get("mode") or "").upper()
    strategy = str(row.get("strategy") or "").lower()
    return mode in {"LIVE", "LIVE_PROBE"} and strategy.startswith("sports_")


def _unresolved_sports(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        if not _is_live_sports(row):
            continue
        if str(row.get("status") or "").upper() in TERMINAL:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        reserved = _d(metadata.get("reserved_usd"))
        filled = _d(row.get("capital_filled") or row.get("capital_wagered"))
        legs_filled = _d(row.get("legs_filled"))
        if reserved > 0 or filled > 0 or legs_filled > 0:
            return row
    return None


def _rule_risk_loss_used(rows: list[dict[str, Any]]) -> D:
    """Conservatively total negative realized P&L carrying a RULE_RISK classification."""
    total = D("0")
    seen: set[str] = set()
    for row in rows:
        if not _is_live_sports(row):
            continue
        trade_id = str(row.get("trade_id") or "")
        if trade_id and trade_id in seen:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        classification = str(metadata.get("classification") or row.get("classification") or "").upper()
        status = str(row.get("status") or "").upper()
        if "RULE_RISK" not in classification or status not in TERMINAL:
            continue
        net = _d(row.get("net_pnl") or row.get("pnl_net") or row.get("realized_pnl"))
        if net < 0:
            total += -net
        if trade_id:
            seen.add(trade_id)
    return total


async def _post(event: dict[str, Any]) -> None:
    if not LEDGER_TOKEN:
        raise RuntimeError("LEDGER_INGEST_TOKEN missing; MLB maker live execution denied")
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.post(
            f"{COMMAND_CENTER}/api/events",
            headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
            json=event,
        )
        response.raise_for_status()


async def reserve(*, row: Mapping[str, Any], certification: Mapping[str, Any], trade_id: str) -> dict[str, Any]:
    rows = await _trades()
    blocked = _unresolved_sports(rows)
    if blocked is not None:
        raise RuntimeError(f"unresolved LIVE sports exposure blocks MLB maker: {blocked.get('trade_id')}")
    loss_used = _rule_risk_loss_used(rows)
    if loss_used >= MAX_REALIZED_LOSS_BUDGET:
        raise RuntimeError(
            f"sports rule-risk realized-loss budget exhausted: used={loss_used} ceiling={MAX_REALIZED_LOSS_BUDGET}"
        )

    market = f"{row.get('low_slug')}|{row.get('high_slug')}"
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-reserved"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RESERVED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": market,
        "asset": str(row.get("player") or row.get("family") or "MLB_PROP"),
        "venues": ["polymarket"],
        "side": "COMPLEMENTARY_MAKER_HEDGE",
        "status": "CAPITAL_RESERVED",
        "amount": "1",
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {
            "reserved_usd": "1",
            "q": 1,
            "classification": "LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE",
            "guaranteed_arbitrage": False,
            "max_realized_loss_budget_usd": str(MAX_REALIZED_LOSS_BUDGET),
            "realized_rule_risk_loss_used_usd": str(loss_used),
            "maker_slug": row.get("maker_slug"),
            "hedge_slug": row.get("hedge_slug"),
            "certification": dict(certification),
        },
    })
    return {"reserved": True, "trade_id": trade_id, "loss_used": str(loss_used), "loss_ceiling": str(MAX_REALIZED_LOSS_BUDGET)}


async def release(*, row: Mapping[str, Any], trade_id: str, reason: str) -> None:
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-released"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RELEASED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": f"{row.get('low_slug')}|{row.get('high_slug')}",
        "asset": str(row.get("player") or row.get("family") or "MLB_PROP"),
        "venues": ["polymarket"],
        "side": "COMPLEMENTARY_MAKER_HEDGE",
        "status": "CAPITAL_RELEASED",
        "amount": "0",
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {
            "reserved_usd": "0",
            "q": 1,
            "classification": "LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE",
            "guaranteed_arbitrage": False,
            "release_reason": reason,
        },
    })

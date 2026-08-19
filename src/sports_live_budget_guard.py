"""Durable cumulative capital guard for autonomous sports live probes.

The guard uses ARB Command Center as the persistent reservation ledger.  A live
order cannot pass this wrapper unless its capital is first reserved through the
protected ingest endpoint.  This makes Railway restarts fail closed instead of
forgetting prior exposure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from typing import Any, Awaitable, Callable

import httpx

import src.sports_trade_reporting as reporting

logger = logging.getLogger("kalshi_arb.sports_live_budget_guard")
D = Decimal

COMMAND_CENTER = os.getenv(
    "ARB_COMMAND_CENTER_URL",
    "https://arb-command-center-production.up.railway.app",
).rstrip("/")
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
CUMULATIVE_BUDGET = D(os.getenv("SPORTS_LIVE_CUMULATIVE_BUDGET_USD", "5"))


class BudgetExhausted(RuntimeError):
    pass


def _d(value: Any, default: str = "0") -> D:
    try:
        return D(str(value))
    except Exception:
        return D(default)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def budget_used_from_trades(rows: list[dict[str, Any]]) -> D:
    """Conservative cumulative capital: max(actual fills, active reservation) per trade."""
    total = D("0")
    for row in rows:
        if str(row.get("mode") or "") not in {"LIVE", "LIVE_PROBE"}:
            continue
        if not str(row.get("strategy") or "").startswith("sports_gk_"):
            continue
        actual = _d(row.get("capital_filled"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        reserved = _d(metadata.get("reserved_usd"))
        if str(row.get("status") or "") == "CAPITAL_RELEASED":
            reserved = D("0")
        total += max(actual, reserved)
    return total


async def current_used(http: httpx.AsyncClient | None = None) -> D:
    owned = http is None
    client = http or httpx.AsyncClient(timeout=4.0)
    try:
        response = await client.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, list):
            raise RuntimeError("command center trades response is not a list")
        return budget_used_from_trades([x for x in body if isinstance(x, dict)])
    finally:
        if owned:
            await client.aclose()


async def _post_required(event: dict[str, Any]) -> None:
    if not LEDGER_TOKEN:
        raise RuntimeError("LEDGER_INGEST_TOKEN missing; live capital reservation denied")
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.post(
            f"{COMMAND_CENTER}/api/events",
            headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
            json=event,
        )
        response.raise_for_status()


async def reserve(*, trade_id: str, strategy: str, context: dict[str, Any], amount: D) -> D:
    if amount <= 0 or amount > CUMULATIVE_BUDGET:
        raise BudgetExhausted(f"invalid reservation amount {amount}")
    used = await current_used()
    remaining = CUMULATIVE_BUDGET - used
    if amount > remaining:
        raise BudgetExhausted(f"sports live cumulative budget exhausted: used={used} remaining={remaining} requested={amount}")
    event = {
        "event_id": _safe_id(f"{trade_id}:capital-reserved"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RESERVED",
        "event_ts": _now(),
        "strategy": strategy,
        "mode": "LIVE_PROBE",
        "market": context.get("settlement_key"),
        "asset": context.get("player"),
        "venues": ["gemini", "kalshi"],
        "side": context.get("direction"),
        "status": "CAPITAL_RESERVED",
        "amount": str(amount),
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {**context, "reserved_usd": str(amount), "cumulative_budget_usd": str(CUMULATIVE_BUDGET)},
    }
    await _post_required(event)
    return used + amount


async def release(*, trade_id: str, strategy: str, context: dict[str, Any]) -> None:
    event = {
        "event_id": _safe_id(f"{trade_id}:capital-released"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RELEASED",
        "event_ts": _now(),
        "strategy": strategy,
        "mode": "LIVE_PROBE",
        "market": context.get("settlement_key"),
        "asset": context.get("player"),
        "venues": ["gemini", "kalshi"],
        "side": context.get("direction"),
        "status": "CAPITAL_RELEASED",
        "amount": "0",
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {**context, "reserved_usd": "0", "cumulative_budget_usd": str(CUMULATIVE_BUDGET)},
    }
    await _post_required(event)


def install(canary_module: Any, *, strategy: str, run_id_env: str) -> None:
    """Wrap the already-reported submit path with required durable reservation."""
    inner: Callable[..., Awaitable[dict[str, Any]]] = canary_module.submit_dual_probe

    async def guarded_submit(gemini: Any, kalshi: Any, gp: dict[str, Any], kp: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        run_id = os.getenv(run_id_env, "").strip()
        if not run_id:
            raise RuntimeError(f"{run_id_env} missing")
        context = dict(reporting._CONTEXT.get(run_id) or {})
        requested = _d(context.get("net_cost"))
        if requested <= 0:
            raise RuntimeError("live capital reservation denied: candidate net_cost missing")
        trade_id = _safe_id(f"sports-gk:{run_id}")
        new_used = await reserve(trade_id=trade_id, strategy=strategy, context=context, amount=requested)
        reporting._CONTEXT.setdefault(run_id, {}).update({
            "reserved_usd": str(requested),
            "cumulative_budget_usd": str(CUMULATIVE_BUDGET),
            "cumulative_budget_after_reservation_usd": str(new_used),
        })
        logger.warning(
            "SPORTS_CAPITAL_RESERVED trade_id=%s requested=%s cumulative_after=%s ceiling=%s",
            trade_id, requested, new_used, CUMULATIVE_BUDGET,
        )
        result = await inner(gemini, kalshi, gp, kp, **kwargs)
        if str(result.get("outcome") or "") == "NEITHER_FILLED":
            try:
                await release(trade_id=trade_id, strategy=strategy, context=reporting._CONTEXT.get(run_id) or context)
                logger.info("SPORTS_CAPITAL_RELEASED trade_id=%s", trade_id)
            except Exception:
                # Fail conservative: a stale reservation reduces future capacity.
                logger.exception("SPORTS_CAPITAL_RELEASE_FAILED trade_id=%s", trade_id)
        return result

    canary_module.submit_dual_probe = guarded_submit

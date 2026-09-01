"""Bounded overnight supervisor for Gemini BTC5m q=1 inventory-maker v3.

This is live-capable only under its own dual-key arm. It serializes campaigns: no
new campaign starts until the prior campaign is either zero-fill/released or has
settled and been reconciled from exact Gemini client IDs + settled positions.

Hard caps are code-level ceilings, not suggestions:
- BTC5m inventory-maker v3 only;
- q=1 per outcome (in child strategy);
- at most 24 executed inventory campaigns;
- at most 8 hours runtime;
- stop once cumulative session settled net P&L <= -$3.00;
- any ambiguous local state, reconciliation mismatch, or child exception hard-stops.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
import os
import time
from typing import Any

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv
from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3 as v3
from src import gemini_short_crypto_inventory_local_budget as budget
from src import gemini_short_crypto_inventory_settlement_reconcile as settle
from src import gemini_short_crypto_local_budget as local_base

OVERNIGHT_LIVE_ENV = "GEMINI_SHORT_CRYPTO_OVERNIGHT_LIVE_Q1"
OVERNIGHT_ID_ENV = "GEMINI_SHORT_CRYPTO_OVERNIGHT_SESSION_ID"
OVERNIGHT_ARM_ENV = "GEMINI_SHORT_CRYPTO_OVERNIGHT_SESSION_ARM"

HARD_MAX_RUNTIME_S = 8 * 60 * 60
HARD_MAX_EXECUTED_CAMPAIGNS = 24
HARD_MAX_REALIZED_LOSS = D("3.00")
SETTLEMENT_TIMEOUT_S = 12 * 60
SETTLEMENT_POLL_S = 5.0
BETWEEN_ATTEMPTS_S = 1.0
VALID_OPEN_STATUSES = {
    "MATCHED_INVENTORY_NOT_SETTLED",
    "ONE_SIDED_OR_IMBALANCED_INVENTORY_NOT_SETTLED",
}


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_BTC5M_INVENTORY_OVERNIGHT "
        + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str),
        flush=True,
    )


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    sid = os.getenv(OVERNIGHT_ID_ENV, "").strip()
    return bool(
        _enabled(OVERNIGHT_LIVE_ENV)
        and sid
        and os.getenv(OVERNIGHT_ARM_ENV, "").strip() == sid
    )


def _clean(value: str, limit: int = 18) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:limit]
    return out or "session"


def child_canary_id(session_id: str, attempt: int) -> str:
    return f"ov3-{_clean(session_id, 12)}-{int(attempt):03d}"


def loss_cap_hit(realized_pnl: D) -> bool:
    return D(str(realized_pnl)) <= -HARD_MAX_REALIZED_LOSS


def settlement_pending(exc: BaseException) -> bool:
    return "settlement not posted yet" in str(exc).lower()


async def _wait_for_settlement() -> dict[str, Any]:
    deadline = time.monotonic() + SETTLEMENT_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            return await settle.reconcile()
        except RuntimeError as exc:
            if not settlement_pending(exc):
                raise
            emit(
                "WAITING_FOR_SETTLEMENT",
                reason="GEMINI_SETTLEMENT_NOT_POSTED",
                seconds_remaining=max(0, int(deadline - time.monotonic())),
                order_entry=False,
            )
            await asyncio.sleep(SETTLEMENT_POLL_S)
    raise RuntimeError("settlement timeout with unresolved local reservation")


async def run() -> None:
    if not live_armed():
        emit("DISARMED", order_entry=False, realized_pnl="0")
        return

    session_id = _clean(os.environ[OVERNIGHT_ID_ENV], 18)
    if local_base.local_open_state() is not None:
        raise RuntimeError("overnight start blocked by unresolved local reservation")
    await budget.assert_clean_account()

    started = time.monotonic()
    session_realized = D("0")
    attempts = 0
    executed_campaigns = 0
    emit(
        "START",
        session_id=session_id,
        max_runtime_seconds=HARD_MAX_RUNTIME_S,
        max_executed_campaigns=HARD_MAX_EXECUTED_CAMPAIGNS,
        max_realized_loss=str(HARD_MAX_REALIZED_LOSS),
        q_per_outcome=1,
        order_entry=True,
    )

    while time.monotonic() - started < HARD_MAX_RUNTIME_S:
        if executed_campaigns >= HARD_MAX_EXECUTED_CAMPAIGNS:
            emit("STOP", reason="MAX_EXECUTED_CAMPAIGNS", executed_campaigns=executed_campaigns, realized_pnl=str(session_realized))
            return
        if loss_cap_hit(session_realized):
            emit("STOP", reason="REALIZED_LOSS_CAP", executed_campaigns=executed_campaigns, realized_pnl=str(session_realized))
            return

        await budget.assert_clean_account()
        attempts += 1
        child_id = child_canary_id(session_id, attempts)
        os.environ[inv.LIVE_ENV] = "YES"
        os.environ[inv.CANARY_ID_ENV] = child_id
        os.environ[inv.CANARY_ARM_ENV] = child_id
        emit(
            "CAMPAIGN_ATTEMPT_START",
            attempt=attempts,
            child_canary_id=child_id,
            executed_campaigns=executed_campaigns,
            realized_pnl=str(session_realized),
            order_entry=True,
        )

        try:
            await v3.run()
        except Exception as exc:
            emit(
                "HARD_HALT",
                reason="CHILD_EXCEPTION",
                exception_type=type(exc).__name__,
                child_canary_id=child_id,
                realized_pnl=str(session_realized),
                order_entry=True,
            )
            raise

        local = local_base.local_open_state()
        if local is None:
            emit(
                "CAMPAIGN_ATTEMPT_NO_OPEN_INVENTORY",
                attempt=attempts,
                child_canary_id=child_id,
                executed_campaigns=executed_campaigns,
                realized_pnl=str(session_realized),
                order_entry=True,
            )
            await asyncio.sleep(BETWEEN_ATTEMPTS_S)
            continue

        status = str(local.get("status") or "")
        if status not in VALID_OPEN_STATUSES:
            emit(
                "HARD_HALT",
                reason="AMBIGUOUS_LOCAL_STATUS",
                status=status,
                trade_id=local.get("trade_id"),
                realized_pnl=str(session_realized),
                order_entry=False,
            )
            raise RuntimeError(f"overnight ambiguous local status: {status}")

        executed_campaigns += 1
        result = await _wait_for_settlement()
        pnl = D(str(result.get("net_profit") or "0"))
        session_realized += pnl
        emit(
            "CAMPAIGN_SETTLED",
            executed_campaigns=executed_campaigns,
            trade_id=result.get("trade_id"),
            yes_fill=result.get("yes_fill"),
            no_fill=result.get("no_fill"),
            matched_inventory=result.get("matched_inventory"),
            resolution_side=result.get("resolution_side"),
            campaign_net_profit=str(pnl),
            session_realized_pnl=str(session_realized),
            local_reservation_cleared=result.get("local_reservation_cleared"),
            order_entry=False,
        )

        if not result.get("local_reservation_cleared"):
            raise RuntimeError("settlement reconciliation did not clear local reservation")
        if loss_cap_hit(session_realized):
            emit(
                "STOP",
                reason="REALIZED_LOSS_CAP",
                executed_campaigns=executed_campaigns,
                realized_pnl=str(session_realized),
                order_entry=False,
            )
            return
        await asyncio.sleep(BETWEEN_ATTEMPTS_S)

    emit(
        "STOP",
        reason="MAX_RUNTIME",
        attempts=attempts,
        executed_campaigns=executed_campaigns,
        realized_pnl=str(session_realized),
        order_entry=False,
    )


if __name__ == "__main__":
    asyncio.run(run())

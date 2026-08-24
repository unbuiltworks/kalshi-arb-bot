"""Isolated one-trade supervisor for the approved crypto settlement-basis canary.

This supervisor deliberately runs a single q=1 attempt with <=$1 deployed capital,
although the user approved a separate $5 maximum-loss research budget. It reuses the
proven campaign settlement/accounting machinery but relabels every authoritative
ledger event as basis-risk research and spawns only the dominance-gated child.

A deterministic CAPITAL_RESERVED row in the authoritative Postgres ledger is the
cross-restart single-use claim. Local /tmp files coordinate only the parent/child
processes inside one Railway container. The production settlement-equivalence hard
stop is not modified or bypassed by the guaranteed-arbitrage service.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import sys
from typing import Any

from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_sol_live_canary_v2 as v2
from src.gemini_kalshi_basis_risk_q1_child import (
    APPROVED_BUDGET,
    BASIS_ARM_ENV,
    BASIS_BUDGET_ENV,
    BASIS_DEADLINE_ENV,
    BASIS_MIN_GAP_ENV,
    BASIS_RECONCILED_ENV,
    DEFAULT_MIN_GAP_BPS,
    _local_state_dir,
)

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_campaign")

BASIS_CAMPAIGN_ID_ENV = "CRYPTO_BASIS_RISK_CAMPAIGN_ID"
STRATEGY = "gemini_kalshi_15m_crypto_basis_risk"
MODE = "LIVE_BASIS_RISK_CANARY"
INITIAL_TRANCHE_USD = D("1")
MAX_TRADES = 1

_ORIGINAL_POST_EVENT = base._post_event
_ORIGINAL_EMIT_EXECUTION = base._emit_execution_events
_ORIGINAL_EMIT_REALIZED = base._emit_realized_event
_CURRENT_CONTEXT: dict[str, Any] | None = None


def _validate_basis_campaign_env() -> tuple[str, datetime, Decimal]:
    if os.getenv(BASIS_ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(f"basis-risk campaign requires {BASIS_ARM_ENV}=YES")
    if os.getenv(BASIS_RECONCILED_ENV, "NO").strip().upper() != "YES":
        raise PermissionError("basis-risk campaign requires prior crypto ledger reconciliation")
    budget = D(os.getenv(BASIS_BUDGET_ENV, "0"))
    if budget != APPROVED_BUDGET:
        raise PermissionError(f"approved basis-risk budget must be exactly {APPROVED_BUDGET}")
    min_gap = D(os.getenv(BASIS_MIN_GAP_ENV, str(DEFAULT_MIN_GAP_BPS)))
    if min_gap < DEFAULT_MIN_GAP_BPS:
        raise PermissionError("basis-risk minimum protective gap cannot be below 10 bp")
    campaign_id = base._clean_campaign_id(os.getenv(BASIS_CAMPAIGN_ID_ENV, ""))
    deadline_raw = os.getenv(BASIS_DEADLINE_ENV, "").strip()
    if not deadline_raw:
        raise RuntimeError(f"{BASIS_DEADLINE_ENV} is required")
    deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    deadline = deadline.astimezone(timezone.utc)
    if datetime.now(timezone.utc) > deadline:
        raise PermissionError("basis-risk campaign deadline already expired")
    return campaign_id, deadline, min_gap


async def _basis_post_event(http, event: dict[str, Any]) -> None:
    """Give basis-risk activity a distinct authoritative ledger identity."""
    payload = dict(event)
    old_trade_id = str(payload.get("trade_id") or "")
    new_trade_id = old_trade_id
    if old_trade_id.startswith("gk15m-"):
        new_trade_id = "gkbasis-" + old_trade_id[len("gk15m-"):]
        payload["trade_id"] = new_trade_id
        event_id = str(payload.get("event_id") or "")
        if event_id.startswith(old_trade_id):
            payload["event_id"] = new_trade_id + event_id[len(old_trade_id):]
    payload["strategy"] = STRATEGY
    payload["mode"] = MODE
    metadata = dict(payload.get("metadata") or {})
    metadata.update({
        "risk_class": "SETTLEMENT_BASIS_RESEARCH",
        "approved_max_loss_budget_usd": str(APPROVED_BUDGET),
        "initial_live_tranche_usd": str(INITIAL_TRANCHE_USD),
        "guaranteed_arb": False,
    })
    if _CURRENT_CONTEXT:
        metadata["basis_risk_context"] = dict(_CURRENT_CONTEXT)
    payload["metadata"] = metadata
    await _ORIGINAL_POST_EVENT(http, payload)


async def _basis_emit_execution(http, run_id: str, record: dict[str, Any]):
    global _CURRENT_CONTEXT
    context = record.get("basis_risk_context")
    if not isinstance(context, dict) or not context.get("authorized"):
        raise PermissionError("execution record missing dominance-safe basis-risk authorization")
    _CURRENT_CONTEXT = dict(context)
    try:
        return await _ORIGINAL_EMIT_EXECUTION(http, run_id, record)
    finally:
        _CURRENT_CONTEXT = None


async def _basis_emit_realized(http, *, run_id, record, costs, settlement):
    global _CURRENT_CONTEXT
    context = record.get("basis_risk_context")
    if not isinstance(context, dict) or not context.get("authorized"):
        raise PermissionError("realization record missing basis-risk authorization context")
    _CURRENT_CONTEXT = dict(context)
    try:
        pnl = await _ORIGINAL_EMIT_REALIZED(
            http,
            run_id=run_id,
            record=record,
            costs=costs,
            settlement=settlement,
        )
    finally:
        _CURRENT_CONTEXT = None
    if D(pnl) < 0:
        logger.critical("CRYPTO_BASIS_RISK_REALIZED_LOSS run_id=%s net_pnl=%s hard_stop=true", run_id, pnl)
    return pnl


async def _obtain_basis_execution(run_id: str, arm_deadline: datetime) -> dict[str, Any]:
    arm, claim, result_path = v2._paths(run_id)
    if result_path.is_file():
        with result_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    if claim.is_file():
        raise RuntimeError(f"basis-risk local claim exists without result for {run_id}")

    child_env = os.environ.copy()
    child_env[v2.RUN_ID_ENV] = run_id
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.gemini_kalshi_basis_risk_q1_child",
        env=child_env,
    )
    wait_task = asyncio.create_task(process.wait())
    try:
        while base._utcnow() <= arm_deadline:
            if result_path.is_file():
                with result_path.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
            if wait_task.done():
                code = wait_task.result()
                raise RuntimeError(f"basis-risk child exited {code} before persistent result")
            await asyncio.sleep(base.CHILD_RESULT_POLL_S)
        raise TimeoutError(f"basis-risk arm deadline reached before execution for {run_id}")
    finally:
        await base._stop_child(process)
        if not wait_task.done():
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
        if arm.is_file() and not claim.is_file():
            try:
                arm.unlink()
            except FileNotFoundError:
                pass


def _install_isolated_campaign_hooks() -> None:
    base._post_event = _basis_post_event
    base._emit_execution_events = _basis_emit_execution
    base._emit_realized_event = _basis_emit_realized
    base._obtain_execution = _obtain_basis_execution


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    campaign_id, deadline, min_gap = _validate_basis_campaign_env()
    _install_isolated_campaign_hooks()

    # Local file state is not relied on for cross-restart uniqueness. The child must
    # atomically insert gkbasis-<run>-capital-reserved before any venue submission.
    v2._state_dir = _local_state_dir

    os.environ[base.CAMPAIGN_ID_ENV] = campaign_id
    os.environ[base.MAX_TRADES_ENV] = str(MAX_TRADES)
    os.environ[base.HARD_BUDGET_ENV] = str(INITIAL_TRANCHE_USD)
    os.environ[v2.ARM_ENV] = "YES"
    os.environ["ENABLE_GEMINI_KALSHI_LIVE_PROBES"] = "YES"
    os.environ["SOL_CANARY_V2_ARM_NOT_AFTER_UTC"] = deadline.isoformat()

    logger.warning(
        "CRYPTO_BASIS_RISK_CAMPAIGN_ENABLED campaign_id=%s max_trades=1 quantity=1 "
        "initial_tranche_usd=%s approved_max_loss_budget_usd=%s min_gap_bps=%s "
        "durable_claim=postgres_capital_reserved guaranteed_arb=false one_unresolved_trade=true",
        campaign_id, INITIAL_TRANCHE_USD, APPROVED_BUDGET, min_gap,
    )
    asyncio.run(base.run())


if __name__ == "__main__":
    main()

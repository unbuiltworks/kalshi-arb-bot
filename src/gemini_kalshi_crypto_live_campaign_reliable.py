"""Retry-safe bounded live campaign with Gemini-first staged execution.

This supervisor preserves the original campaign's settlement, dashboard, budget and
hard-halt rules. The only new behavior is that PRIMARY_NOT_FILLED (Gemini FOK canceled
with zero fill before any Kalshi order is sent) is a benign execution miss. It gets a
fresh deterministic run id and the campaign continues scanning. Any actual one-leg
fill or unresolved submission ambiguity still halts fail-closed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import sys

import httpx

from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_sol_live_canary_v2 as v2

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_live_campaign_reliable")
MAX_EXECUTION_ATTEMPTS_ENV = "CRYPTO_CANARY_MAX_EXECUTION_ATTEMPTS"


def _benign_primary_miss(result: dict) -> bool:
    return bool(
        str(result.get("outcome") or "") == "PRIMARY_NOT_FILLED"
        and not bool(result.get("hard_halt_required"))
        and str((result.get("gemini") or {}).get("filled_quantity") or "0") == "0"
        and result.get("kalshi") is None
    )


async def _obtain_execution(run_id: str, arm_deadline: datetime) -> dict:
    arm, claim, result_path = v2._paths(run_id)
    if result_path.is_file():
        with result_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    if claim.is_file():
        raise RuntimeError(f"attempt claim exists without result for {run_id}")

    child_env = os.environ.copy()
    child_env[v2.RUN_ID_ENV] = run_id
    child_env[v2.ARM_ENV] = "YES"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.gemini_kalshi_crypto_campaign_child_reliable",
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
                raise RuntimeError(f"reliable canary child exited {code} before persistent result")
            await asyncio.sleep(base.CHILD_RESULT_POLL_S)
        raise TimeoutError(f"campaign arm deadline reached before execution for {run_id}")
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


async def run() -> None:
    campaign_id = base._clean_campaign_id(os.getenv(base.CAMPAIGN_ID_ENV, ""))
    max_trades = int(os.getenv(base.MAX_TRADES_ENV, "5"))
    hard_budget = D(os.getenv(base.HARD_BUDGET_ENV, "5"))
    max_execution_attempts = int(os.getenv(MAX_EXECUTION_ATTEMPTS_ENV, str(max_trades * 5)))

    if not 1 <= max_trades <= 5:
        raise ValueError("campaign max trades must be 1..5")
    if hard_budget <= 0 or hard_budget > D("5"):
        raise ValueError("campaign hard budget must be >0 and <=5")
    if not max_trades <= max_execution_attempts <= 50:
        raise ValueError("max execution attempts must be between max_trades and 50")
    if os.getenv(v2.ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError("campaign requires explicit SOL_CANARY_V2_ARM=YES")
    if os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() != "YES":
        raise PermissionError("campaign requires ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES")

    deadline_raw = os.getenv("SOL_CANARY_V2_ARM_NOT_AFTER_UTC", "").strip()
    if not deadline_raw:
        raise RuntimeError("SOL_CANARY_V2_ARM_NOT_AFTER_UTC is required")
    arm_deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    if base._utcnow() > arm_deadline:
        raise RuntimeError("campaign arm deadline already expired")

    base._disarm_bootstrap_run(campaign_id)
    path = base._state_path(campaign_id)
    state = base._load_state(path, campaign_id, max_trades, hard_budget)
    state.setdefault("execution_attempt_count", len(state.get("attempts", [])))
    state.setdefault("execution_misses", [])
    state["max_execution_attempts"] = max_execution_attempts
    base._write_state(path, state)

    logger.warning(
        "CRYPTO_LIVE_RELIABLE_CAMPAIGN_ENABLED campaign_id=%s max_trades=%s max_execution_attempts=%s "
        "hard_budget=%s execution_policy=GEMINI_FIRST_TERMINAL_THEN_KALSHI quantity=1 "
        "min_profit=0.03 min_double_boundary_bps=10 execution_delay_ms=50 "
        "benign_primary_no_fill_retry=true one_unresolved_trade=true dashboard_realization_required=true",
        campaign_id, max_trades, max_execution_attempts, hard_budget,
    )

    async with httpx.AsyncClient(timeout=20.0) as http:
        while len(state["attempts"]) < max_trades and base._utcnow() <= arm_deadline:
            execution_count = int(state.get("execution_attempt_count") or 0)
            if execution_count >= max_execution_attempts:
                state["status"] = "COMPLETE_MAX_EXECUTION_ATTEMPTS"
                state["updated_at_utc"] = base._iso()
                base._write_state(path, state)
                break

            cumulative_capital = D(str(state.get("cumulative_capital_usd") or "0"))
            if cumulative_capital + D("1") > hard_budget:
                state["status"] = "COMPLETE_BUDGET_RESERVE"
                state["updated_at_utc"] = base._iso()
                base._write_state(path, state)
                break

            execution_number = execution_count + 1
            trade_number = len(state["attempts"]) + 1
            run_id = base._run_id(campaign_id, execution_number)
            state["execution_attempt_count"] = execution_number
            state["status"] = "WAITING_EXECUTION"
            state["active_run_id"] = run_id
            state["updated_at_utc"] = base._iso()
            base._write_state(path, state)
            logger.warning(
                "CRYPTO_LIVE_RELIABLE_CAMPAIGN_ARMING_CHILD run_id=%s execution_attempt=%s target_trade=%s",
                run_id, execution_number, trade_number,
            )

            try:
                record = await _obtain_execution(run_id, arm_deadline)
                result = record.get("result") or {}

                if _benign_primary_miss(result):
                    state["execution_misses"].append({
                        "execution_attempt": execution_number,
                        "target_trade": trade_number,
                        "run_id": run_id,
                        "completed_at_utc": str(record.get("completed_at_utc") or base._iso()),
                        "outcome": "PRIMARY_NOT_FILLED",
                        "gemini_status": (result.get("gemini") or {}).get("status"),
                        "gemini_reason": (result.get("gemini") or {}).get("reason"),
                    })
                    state.pop("active_run_id", None)
                    state["status"] = "RUNNING"
                    state["updated_at_utc"] = base._iso()
                    base._write_state(path, state)
                    logger.warning(
                        "CRYPTO_LIVE_RELIABLE_BENIGN_NO_FILL run_id=%s execution_attempt=%s "
                        "kalshi_order_sent=false continuing=true",
                        run_id, execution_number,
                    )
                    continue

                if str(result.get("outcome") or "") != "BOTH_FILLED" or bool(result.get("hard_halt_required")):
                    await base._emit_execution_events(http, run_id, record)
                    raise RuntimeError(f"live execution unsafe outcome={result.get('outcome')}")

                costs = await base._emit_execution_events(http, run_id, record)
                state["status"] = "WAITING_SETTLEMENT"
                state["updated_at_utc"] = base._iso()
                state["pending_capital_usd"] = str(costs["capital"])
                base._write_state(path, state)

                gp = record.get("gemini_payload") or {}
                kp = record.get("kalshi_payload") or {}
                settlement = await base._official_settlement(
                    http,
                    gemini_symbol=str(gp.get("symbol") or ""),
                    kalshi_ticker=str(kp.get("ticker") or ""),
                )
                net_pnl = await base._emit_realized_event(
                    http,
                    run_id=run_id,
                    record=record,
                    costs=costs,
                    settlement=settlement,
                )

                state["attempts"].append({
                    "attempt": trade_number,
                    "execution_attempt": execution_number,
                    "run_id": run_id,
                    "completed_at_utc": str(record.get("completed_at_utc") or base._iso()),
                    "execution_outcome": result.get("outcome"),
                    "execution_policy": result.get("execution_policy"),
                    "capital_usd": str(costs["capital"]),
                    "realized_net_pnl_usd": str(net_pnl),
                    "settlement": settlement,
                })
                state["cumulative_capital_usd"] = str(cumulative_capital + costs["capital"])
                state["cumulative_realized_net_pnl_usd"] = str(
                    D(str(state.get("cumulative_realized_net_pnl_usd") or "0")) + net_pnl
                )
                state.pop("pending_capital_usd", None)
                state.pop("active_run_id", None)
                state["status"] = "RUNNING"
                state["updated_at_utc"] = base._iso()
                base._write_state(path, state)
                logger.warning(
                    "CRYPTO_LIVE_RELIABLE_CAMPAIGN_REALIZED run_id=%s net_pnl=%s capital=%s "
                    "venue_outcomes_agree=%s completed_trades=%s execution_attempts=%s",
                    run_id, net_pnl, costs["capital"], settlement["venue_outcomes_agree"],
                    len(state["attempts"]), execution_number,
                )

                if not bool(settlement["venue_outcomes_agree"]):
                    state["status"] = "HALTED_SETTLEMENT_DISAGREEMENT"
                    state["updated_at_utc"] = base._iso()
                    base._write_state(path, state)
                    logger.critical("CRYPTO_LIVE_RELIABLE_CAMPAIGN_HALT settlement_disagreement run_id=%s", run_id)
                    return
            except Exception as exc:
                state["status"] = "HALTED_FAIL_CLOSED"
                state["halt_reason"] = f"{type(exc).__name__}: {str(exc)[:400]}"
                state["updated_at_utc"] = base._iso()
                base._write_state(path, state)
                logger.exception("CRYPTO_LIVE_RELIABLE_CAMPAIGN_HALT_FAIL_CLOSED run_id=%s", run_id)
                return

    if state.get("status") == "RUNNING":
        state["status"] = "COMPLETE_MAX_TRADES" if len(state["attempts"]) >= max_trades else "COMPLETE_DEADLINE"
        state["updated_at_utc"] = base._iso()
        base._write_state(path, state)
    logger.warning(
        "CRYPTO_LIVE_RELIABLE_CAMPAIGN_COMPLETE campaign_id=%s status=%s trades=%s "
        "execution_attempts=%s benign_misses=%s capital=%s realized_net_pnl=%s",
        campaign_id,
        state.get("status"),
        len(state.get("attempts", [])),
        state.get("execution_attempt_count"),
        len(state.get("execution_misses", [])),
        state.get("cumulative_capital_usd"),
        state.get("cumulative_realized_net_pnl_usd"),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()

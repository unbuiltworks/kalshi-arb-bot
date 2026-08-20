"""One-trade q=5 scale campaign gated by clean q=1 production evidence.

The campaign never graduates on gross/shadow P&L.  Before q=5 can arm it requires a
specific parallel q=1 campaign state showing enough fully settled clean trades, no
halt, no active lanes, no reserved capital, and no unsettled venue capital.  It then
allows only one successful q=5 trade, retrying only known-zero Gemini primary misses.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any

import httpx

from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_crypto_parallel_campaign as parallel
from src import gemini_kalshi_q5_live_canary as q5
from src import gemini_kalshi_sol_live_canary_v2 as v2
from src.gemini_kalshi_scaled_live_accounting import (
    emit_scaled_execution_events,
    emit_scaled_realized_event,
)

D = Decimal
Q = D("5")
Q5_CAMPAIGN_ID_ENV = "CRYPTO_Q5_CAMPAIGN_ID"
REQUIRED_Q1_CAMPAIGN_ENV = "CRYPTO_Q5_REQUIRED_Q1_CAMPAIGN_ID"
MIN_CLEAN_Q1_TRADES_ENV = "CRYPTO_Q5_MIN_CLEAN_Q1_TRADES"
MAX_Q5_EXECUTION_ATTEMPTS_ENV = "CRYPTO_Q5_MAX_EXECUTION_ATTEMPTS"
Q5_STATE_DIR = Path(os.getenv("CRYPTO_Q5_STATE_DIR", "/data/crypto-q5"))
logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_q5_scale_campaign")


def graduation_from_q1_state(state: dict[str, Any], *, min_clean_trades: int) -> tuple[bool, str]:
    if bool(state.get("halted")):
        return False, "q1_campaign_halted"
    completed = int(state.get("completed_total") or 0)
    if completed < int(min_clean_trades):
        return False, f"q1_clean_trades_{completed}_lt_{min_clean_trades}"
    if D(str(state.get("unsettled_deployed_usd") or "0")) != 0:
        return False, "q1_unsettled_deployed_nonzero"
    if D(str(state.get("reserved_capital_usd") or "0")) != 0:
        return False, "q1_reserved_capital_nonzero"
    venue_unsettled = state.get("venue_unsettled_usd") or {}
    venue_reserved = state.get("venue_reserved_usd") or {}
    for venue in ("gemini", "kalshi"):
        if D(str(venue_unsettled.get(venue) or "0")) != 0:
            return False, f"q1_{venue}_unsettled_nonzero"
        if D(str(venue_reserved.get(venue) or "0")) != 0:
            return False, f"q1_{venue}_reserved_nonzero"
    lanes = state.get("lanes") or {}
    for asset in parallel.ASSETS:
        lane = lanes.get(asset) or {}
        if lane.get("active_run_id"):
            return False, f"q1_active_lane_{asset}"
    return True, "clean_q1_graduation_evidence"


def _load_q1_graduation(campaign_id: str, min_clean_trades: int) -> dict[str, Any]:
    path = parallel.STATE_DIR / f"{campaign_id}.json"
    if not path.is_file():
        raise RuntimeError(f"required q1 campaign state not found: {path}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if str(state.get("campaign_id") or "") != campaign_id:
        raise RuntimeError("required q1 campaign state id mismatch")
    ok, reason = graduation_from_q1_state(state, min_clean_trades=min_clean_trades)
    if not ok:
        raise RuntimeError(f"q5 graduation denied: {reason}")
    return state


def _q5_state_path(campaign_id: str) -> Path:
    Q5_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return Q5_STATE_DIR / f"{campaign_id}.json"


def _write_state(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    tmp.replace(path)


async def _obtain_q5_execution(run_id: str, arm_deadline: datetime) -> dict[str, Any]:
    arm, claim, result_path = v2._paths(run_id)
    if result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8"))
    if claim.is_file():
        raise RuntimeError(f"q5 attempt claim exists without result for {run_id}")
    child_env = os.environ.copy()
    child_env[v2.RUN_ID_ENV] = run_id
    child_env[v2.ARM_ENV] = "YES"
    child_env[q5.Q5_ARM_ENV] = "YES"
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "src.gemini_kalshi_q5_live_canary", env=child_env,
    )
    wait_task = asyncio.create_task(process.wait())
    try:
        while base._utcnow() <= arm_deadline:
            if result_path.is_file():
                return json.loads(result_path.read_text(encoding="utf-8"))
            if wait_task.done():
                raise RuntimeError(f"q5 child exited {wait_task.result()} before persistent result run_id={run_id}")
            await asyncio.sleep(base.CHILD_RESULT_POLL_S)
        raise TimeoutError(f"q5 campaign arm deadline reached before execution for {run_id}")
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
    campaign_id = base._clean_campaign_id(os.getenv(Q5_CAMPAIGN_ID_ENV, ""))
    required_q1 = base._clean_campaign_id(os.getenv(REQUIRED_Q1_CAMPAIGN_ENV, ""))
    min_clean = int(os.getenv(MIN_CLEAN_Q1_TRADES_ENV, "5"))
    max_attempts = int(os.getenv(MAX_Q5_EXECUTION_ATTEMPTS_ENV, "10"))
    if not 3 <= min_clean <= 20:
        raise ValueError("q5 minimum clean q1 trades must be 3..20")
    if not 1 <= max_attempts <= 25:
        raise ValueError("q5 max execution attempts must be 1..25")
    if os.getenv(q5.Q5_ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(f"q5 scale campaign requires {q5.Q5_ARM_ENV}=YES")
    if os.getenv(v2.ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError("q5 scale campaign requires SOL_CANARY_V2_ARM=YES")
    if os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() != "YES":
        raise PermissionError("q5 scale campaign requires live probe environment gate")

    deadline_raw = os.getenv(q5.DEADLINE_ENV, "").strip()
    if not deadline_raw:
        raise RuntimeError(f"{q5.DEADLINE_ENV} is required")
    arm_deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    if base._utcnow() > arm_deadline:
        raise RuntimeError("q5 scale campaign arm deadline already expired")

    q1_state = _load_q1_graduation(required_q1, min_clean)
    path = _q5_state_path(campaign_id)
    if path.exists():
        raise RuntimeError("q5 campaign state already exists; reconcile before any restart")
    state = {
        "campaign_id": campaign_id,
        "required_q1_campaign_id": required_q1,
        "q1_completed_clean_trades": int(q1_state.get("completed_total") or 0),
        "created_at_utc": base._iso(),
        "updated_at_utc": base._iso(),
        "status": "RUNNING",
        "quantity": "5",
        "max_successful_trades": 1,
        "max_execution_attempts": max_attempts,
        "execution_attempts": 0,
        "benign_primary_misses": 0,
        "realized_net_pnl_usd": None,
    }
    _write_state(path, state)
    logger.warning(
        "CRYPTO_Q5_SCALE_CAMPAIGN_ENABLED campaign_id=%s required_q1=%s clean_q1_trades=%s "
        "quantity=5 max_successful_trades=1 max_execution_attempts=%s hard_budget=5 "
        "min_profit_per_contract=0.03 min_total_profit=0.15 min_double_boundary_bps=10 "
        "execution_delay_ms=50 hedge_cushion_max_cents=2 graduation_gate=true",
        campaign_id, required_q1, state["q1_completed_clean_trades"], max_attempts,
    )

    async with httpx.AsyncClient(timeout=20.0) as http:
        for attempt in range(1, max_attempts + 1):
            # Re-read q1 evidence before each scale attempt; any retroactive halt/state
            # inconsistency fails closed before a new q5 child can arm.
            _load_q1_graduation(required_q1, min_clean)
            run_id = v2._clean_run_id(f"{campaign_id}-{attempt:02d}")
            state["execution_attempts"] = attempt
            state["active_run_id"] = run_id
            state["status"] = "WAITING_EXECUTION"
            state["updated_at_utc"] = base._iso()
            _write_state(path, state)
            logger.warning("CRYPTO_Q5_SCALE_ARMING_CHILD run_id=%s attempt=%s", run_id, attempt)
            try:
                record = await _obtain_q5_execution(run_id, arm_deadline)
                result = record.get("result") or {}
                if reliable._benign_primary_miss(result):
                    state["benign_primary_misses"] += 1
                    state.pop("active_run_id", None)
                    state["status"] = "RUNNING"
                    state["updated_at_utc"] = base._iso()
                    _write_state(path, state)
                    logger.warning(
                        "CRYPTO_Q5_SCALE_BENIGN_NO_FILL run_id=%s kalshi_order_sent=false continuing=true",
                        run_id,
                    )
                    continue

                if str(result.get("outcome") or "") != "BOTH_FILLED" or bool(result.get("hard_halt_required")):
                    try:
                        await emit_scaled_execution_events(http, run_id, record, required_quantity=Q)
                    except Exception:
                        pass
                    raise RuntimeError(f"unsafe q5 execution outcome={result.get('outcome')}")

                costs = await emit_scaled_execution_events(http, run_id, record, required_quantity=Q)
                if D(costs["capital"]) > D("5"):
                    raise RuntimeError(f"q5 actual capital exceeded hard budget: {costs['capital']}")
                state["status"] = "WAITING_SETTLEMENT"
                state["capital_usd"] = str(costs["capital"])
                state["updated_at_utc"] = base._iso()
                _write_state(path, state)

                gp = record.get("gemini_payload") or {}
                kp = record.get("kalshi_payload") or {}
                settlement = await base._official_settlement(
                    http,
                    gemini_symbol=str(gp.get("symbol") or ""),
                    kalshi_ticker=str(kp.get("ticker") or ""),
                )
                net_pnl = await emit_scaled_realized_event(
                    http, run_id=run_id, record=record, costs=costs,
                    settlement=settlement, required_quantity=Q,
                )
                state.pop("active_run_id", None)
                state["status"] = "COMPLETE_ONE_Q5_TRADE" if settlement["venue_outcomes_agree"] else "HALTED_SETTLEMENT_DISAGREEMENT"
                state["realized_net_pnl_usd"] = str(net_pnl)
                state["settlement"] = settlement
                state["updated_at_utc"] = base._iso()
                _write_state(path, state)
                logger.warning(
                    "CRYPTO_Q5_SCALE_REALIZED run_id=%s net_pnl=%s capital=%s venue_outcomes_agree=%s",
                    run_id, net_pnl, costs["capital"], settlement["venue_outcomes_agree"],
                )
                return
            except Exception as exc:
                state["status"] = "HALTED_FAIL_CLOSED"
                state["halt_reason"] = f"{type(exc).__name__}: {str(exc)[:400]}"
                state["updated_at_utc"] = base._iso()
                _write_state(path, state)
                logger.exception("CRYPTO_Q5_SCALE_HALT_FAIL_CLOSED run_id=%s", run_id)
                return

    state.pop("active_run_id", None)
    state["status"] = "COMPLETE_MAX_EXECUTION_ATTEMPTS"
    state["updated_at_utc"] = base._iso()
    _write_state(path, state)
    logger.warning("CRYPTO_Q5_SCALE_COMPLETE_NO_TRADE campaign_id=%s attempts=%s", campaign_id, max_attempts)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()

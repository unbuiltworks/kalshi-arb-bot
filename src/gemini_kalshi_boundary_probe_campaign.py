"""Bounded live supervisor for the experimental 5-<10 bp q=1 cohort.

This intentionally reuses the proven reliable live-campaign settlement/accounting
loop while swapping only the order-capable child entrypoint. The cohort is kept
separate from the >=10 bp core campaign by its own campaign id and explicit arm.

Hard limits:
* q=1 only (enforced by the child)
* at most five completed trades
* at most $5 cumulative deployed capital
* one unresolved trade at a time
* settlement disagreement or any unsafe execution halts fail-closed
* experimental trades use a distinct ledger strategy/mode and are not q5-graduation
  evidence for the core campaign
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import sys

from src import gemini_kalshi_boundary_probe_5to10bp as probe
from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_sol_live_canary_v2 as v2

logger = logging.getLogger("kalshi_arb.gemini_kalshi_boundary_probe_campaign")

CAMPAIGN_ID_ENV = "CRYPTO_BOUNDARY_PROBE_CAMPAIGN_ID"
MAX_TRADES_ENV = "CRYPTO_BOUNDARY_PROBE_MAX_TRADES"
MAX_EXECUTION_ATTEMPTS_ENV = "CRYPTO_BOUNDARY_PROBE_MAX_EXECUTION_ATTEMPTS"
HARD_BUDGET_ENV = "CRYPTO_BOUNDARY_PROBE_HARD_BUDGET_USD"
DEADLINE_ENV = "SOL_CANARY_V2_ARM_NOT_AFTER_UTC"
BOUNDARY_STRATEGY = "gemini_kalshi_15m_crypto_boundary_probe"
BOUNDARY_MODE = "LIVE_BOUNDARY_PROBE_5_TO_LT_10BP"
_ORIGINAL_POST_EVENT = base._post_event


async def _post_boundary_event(http, event: dict) -> None:
    """Preserve ledger math while making the experimental cohort unmistakable."""
    payload = dict(event)
    payload["strategy"] = BOUNDARY_STRATEGY
    payload["mode"] = BOUNDARY_MODE
    metadata = dict(payload.get("metadata") or {})
    metadata.update({
        "boundary_probe_band": "5_to_lt_10_bp",
        "core_campaign": False,
        "q5_graduation_evidence": False,
    })
    payload["metadata"] = metadata
    await _ORIGINAL_POST_EVENT(http, payload)


def _configure_campaign_env() -> dict[str, str | int]:
    if os.getenv(probe.ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(f"boundary probe campaign requires {probe.ARM_ENV}=YES")
    if os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() != "YES":
        raise PermissionError("boundary probe campaign requires live probe environment gate")

    campaign_id = base._clean_campaign_id(os.getenv(CAMPAIGN_ID_ENV, ""))
    max_trades = int(os.getenv(MAX_TRADES_ENV, "5"))
    max_execution_attempts = int(os.getenv(MAX_EXECUTION_ATTEMPTS_ENV, "25"))
    hard_budget = os.getenv(HARD_BUDGET_ENV, "5").strip()

    if not 1 <= max_trades <= 5:
        raise ValueError("boundary probe max trades must be 1..5")
    if not max_trades <= max_execution_attempts <= 25:
        raise ValueError("boundary probe max execution attempts must be between max_trades and 25")
    try:
        hard_budget_value = float(hard_budget)
    except ValueError as exc:
        raise ValueError("boundary probe hard budget must be numeric") from exc
    if not 0 < hard_budget_value <= 5:
        raise ValueError("boundary probe hard budget must be >0 and <=5")

    deadline_raw = os.getenv(DEADLINE_ENV, "").strip()
    if not deadline_raw:
        raise RuntimeError(f"{DEADLINE_ENV} is required")
    deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    if base._utcnow() > deadline:
        raise RuntimeError("boundary probe arm deadline already expired")

    # Reuse the proven reliable supervisor with a unique state namespace and strict
    # bounds. These assignments affect only this process and its children.
    os.environ[base.CAMPAIGN_ID_ENV] = campaign_id
    os.environ[base.MAX_TRADES_ENV] = str(max_trades)
    os.environ[base.HARD_BUDGET_ENV] = hard_budget
    os.environ[reliable.MAX_EXECUTION_ATTEMPTS_ENV] = str(max_execution_attempts)
    os.environ[v2.ARM_ENV] = "YES"

    return {
        "campaign_id": campaign_id,
        "max_trades": max_trades,
        "max_execution_attempts": max_execution_attempts,
        "hard_budget_usd": hard_budget,
    }


async def _obtain_boundary_execution(run_id: str, arm_deadline: datetime) -> dict:
    arm, claim, result_path = v2._paths(run_id)
    if result_path.is_file():
        with result_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    if claim.is_file():
        raise RuntimeError(f"attempt claim exists without result for {run_id}")

    child_env = os.environ.copy()
    child_env[v2.RUN_ID_ENV] = run_id
    child_env[v2.ARM_ENV] = "YES"
    child_env["CRYPTO_BOUNDARY_PROBE_RUN_ID"] = run_id
    child_env[probe.ARM_ENV] = "YES"

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.gemini_kalshi_boundary_probe_5to10bp",
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
                raise RuntimeError(f"boundary probe child exited {code} before persistent result")
            await asyncio.sleep(base.CHILD_RESULT_POLL_S)
        raise TimeoutError(f"boundary probe arm deadline reached before execution for {run_id}")
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    cfg = _configure_campaign_env()
    reliable._obtain_execution = _obtain_boundary_execution
    base._post_event = _post_boundary_event
    logger.warning(
        "CRYPTO_BOUNDARY_PROBE_CAMPAIGN_ENABLED campaign_id=%s band=5_to_lt_10_bp "
        "max_trades=%s max_execution_attempts=%s hard_budget_usd=%s quantity=1 "
        "min_profit=0.03 execution_delay_ms=50 ledger_strategy=%s ledger_mode=%s "
        "separate_from_core=true q5_graduation_evidence=false",
        cfg["campaign_id"], cfg["max_trades"], cfg["max_execution_attempts"], cfg["hard_budget_usd"],
        BOUNDARY_STRATEGY, BOUNDARY_MODE,
    )
    reliable.main()


if __name__ == "__main__":
    main()

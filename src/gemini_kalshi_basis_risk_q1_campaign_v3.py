"""Hardened basis-risk q1 campaign with diagnostic gate telemetry.

This wrapper preserves the v2 campaign's reconciliation, one-trade/<= $1 capital
ceiling, explicit $5 approved research budget, and every existing live gate. The only
change is that its child process uses v3, which best-effort records deduplicated
threshold-gate observations to the authoritative ledger for later settlement study.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from src import gemini_kalshi_basis_risk_q1_campaign_v2 as base
from src import gemini_kalshi_crypto_live_campaign as crypto
from src import gemini_kalshi_sol_live_canary_v2 as v2


async def _obtain_telemetry_execution(run_id: str, arm_deadline) -> dict[str, Any]:
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
        "src.gemini_kalshi_basis_risk_q1_child_v3",
        env=child_env,
    )
    wait_task = asyncio.create_task(process.wait())
    try:
        while crypto._utcnow() <= arm_deadline:
            if result_path.is_file():
                with result_path.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
            if wait_task.done():
                code = wait_task.result()
                raise RuntimeError(f"telemetry basis-risk child exited {code} before persistent result")
            await asyncio.sleep(crypto.CHILD_RESULT_POLL_S)
        raise TimeoutError(f"basis-risk arm deadline reached before execution for {run_id}")
    finally:
        await crypto._stop_child(process)
        if not wait_task.done():
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
        if arm.is_file() and not claim.is_file():
            try:
                arm.unlink()
            except FileNotFoundError:
                pass


def main() -> None:
    base._obtain_hardened_execution = _obtain_telemetry_execution
    base.main()


if __name__ == "__main__":
    main()

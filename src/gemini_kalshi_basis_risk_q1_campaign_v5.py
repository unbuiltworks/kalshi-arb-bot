"""One-trade campaign wrapper for promoted sequential q=1 basis probes.

Each invocation still owns at most one q=1 trade and <=$1 capital. The v5 child adds
Postgres-enforced sequential promotion inside the already-approved $5 research budget.
A separate rollover supervisor may launch fresh short-lived campaign leases only while
the authoritative ledger remains clean and promotion remains allowed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

from src import gemini_kalshi_basis_risk_dynamic_policy as dynamic_policy
from src import gemini_kalshi_basis_risk_q1_campaign as campaign
from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_basis_risk_q1_child_v5 as child_v5
from src import gemini_kalshi_crypto_live_campaign as crypto
from src import gemini_kalshi_sol_live_canary_v2 as v2

logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_campaign_v5")


async def _obtain_promoted_execution(run_id: str, arm_deadline: datetime) -> dict[str, Any]:
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
        "src.gemini_kalshi_basis_risk_q1_child_v5",
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
                raise RuntimeError(f"promoted basis-risk child exited {code} before persistent result")
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


def install() -> None:
    campaign.DEFAULT_MIN_GAP_BPS = dynamic_policy.DYNAMIC_MIN_GAP_BPS
    child.MODE = child_v2.LEDGER_MODE
    campaign.MODE = child_v2.LEDGER_MODE
    campaign._obtain_basis_execution = _obtain_promoted_execution


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    child_v2.assert_prior_ledger_reconciled()
    os.environ[child.BASIS_RECONCILED_ENV] = "YES"
    with child_v2._connect() as conn:
        state = child_v5.promoted_state(conn)
    allowed, reason = child_v5.automatic_rollover_allowed(state)
    if not allowed:
        raise PermissionError(f"promoted basis campaign blocked before boot: {reason}")
    install()
    logger.warning(
        "CRYPTO_BASIS_RISK_V5_CAMPAIGN_ENABLED ledger_mode=%s promoted_probe_limit=%s reservations=%s realized=%s realized_net_pnl=%s tranche_usd=%s approved_budget_usd=%s guaranteed_arb=false child=v5_dynamic parent_min_gap_bps=%s",
        child_v2.LEDGER_MODE,
        child_v5.PROMOTED_PROBE_LIMIT,
        state["reservations"],
        state["realized"],
        state["realized_net_pnl"],
        child.INITIAL_TRANCHE_USD,
        child.APPROVED_BUDGET,
        dynamic_policy.DYNAMIC_MIN_GAP_BPS,
    )
    campaign.main()


if __name__ == "__main__":
    main()

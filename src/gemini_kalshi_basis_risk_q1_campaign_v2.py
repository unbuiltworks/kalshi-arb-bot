"""Hardened supervisor for the first approved q=1 settlement-basis live probe.

The underlying campaign remains one trade / <=$1 capital and retains the proven q=1
execution, settlement and authoritative P&L machinery. This entrypoint adds database-
verified reconciliation, uses the hardened v2 child, and writes schema-valid LIVE_PROBE
ledger mode while keeping basis-risk classification explicit in strategy/metadata.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

from src import gemini_kalshi_basis_risk_q1_campaign as campaign
from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_crypto_live_campaign as crypto
from src import gemini_kalshi_sol_live_canary_v2 as v2

logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_campaign_v2")


async def _obtain_hardened_execution(run_id: str, arm_deadline: datetime) -> dict[str, Any]:
    """Launch only the DB-hardened basis-risk child and wait for its persisted result."""
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
        "src.gemini_kalshi_basis_risk_q1_child_v2",
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
                raise RuntimeError(f"hardened basis-risk child exited {code} before persistent result")
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
    # The ledger schema accepts LIVE_PROBE. Risk identity stays explicit in the unique
    # strategy plus metadata, so this is not mislabeled as guaranteed arbitrage.
    child.MODE = child_v2.LEDGER_MODE
    campaign.MODE = child_v2.LEDGER_MODE
    campaign._obtain_basis_execution = _obtain_hardened_execution


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    child_v2.assert_prior_ledger_reconciled()
    os.environ[child.BASIS_RECONCILED_ENV] = "YES"
    install()
    logger.warning(
        "CRYPTO_BASIS_RISK_V2_ENABLED ledger_mode=%s initial_probe_limit=%s "
        "initial_tranche_usd=%s approved_max_loss_budget_usd=%s guaranteed_arb=false",
        child_v2.LEDGER_MODE,
        child_v2.INITIAL_PROBE_LIMIT,
        child.INITIAL_TRANCHE_USD,
        child.APPROVED_BUDGET,
    )
    campaign.main()


if __name__ == "__main__":
    main()

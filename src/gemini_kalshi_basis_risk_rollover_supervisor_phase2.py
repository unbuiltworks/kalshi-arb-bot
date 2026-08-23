"""Phase-2 rollover supervisor for sequential q=1 basis-risk research.

This module is not the production entrypoint.  Activation requires both an explicit
entrypoint change and CRYPTO_BASIS_RISK_PHASE2_APPROVED=YES.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import sys
import time

from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_phase2 as phase2
from src import gemini_kalshi_basis_risk_rollover_supervisor as base

logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_rollover_supervisor_phase2")


async def _run_one_lease(sequence: int, lease_minutes: int):
    now = datetime.now(timezone.utc)
    campaign_id = base._campaign_id(now, sequence)
    deadline = now + timedelta(minutes=lease_minutes)
    env = os.environ.copy()
    env[child.BASIS_ARM_ENV] = "YES"
    env[child.BASIS_RECONCILED_ENV] = "YES"
    env[child.BASIS_DEADLINE_ENV] = deadline.isoformat()
    env["CRYPTO_BASIS_RISK_CAMPAIGN_ID"] = campaign_id
    env[phase2.APPROVAL_ENV] = "YES"
    started = time.monotonic()
    logger.warning(
        "CRYPTO_BASIS_RISK_PHASE2_LEASE_START campaign_id=%s deadline=%s lease_minutes=%s q=1 approved_budget_usd=%s guaranteed_arb=false",
        campaign_id,
        deadline.isoformat(),
        lease_minutes,
        child.APPROVED_BUDGET,
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.gemini_kalshi_basis_risk_q1_campaign_phase2",
        env=env,
    )
    code = await process.wait()
    elapsed = time.monotonic() - started
    return code, elapsed, campaign_id, deadline


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    phase2.install()
    base._run_one_lease = _run_one_lease
    asyncio.run(base.main_async())


if __name__ == "__main__":
    main()

"""v7 basis-risk supervisor: propagate one-lane execution incidents globally.

This is a containment wrapper around v6.  The v6 asset loop already refuses fresh
reservations while Postgres reports an in-flight/unpaired execution, but a child lane
that discovers a reservation without a paired or realized resolution returns locally.
With multiple ETH/SOL/XRP tasks, that can leave sibling scanners alive after a hard
execution incident.  v7 converts that specific post-return state into a supervisor-level
exception so the existing gather/finally path cancels every sibling worker.

No economics, authorization, venue ordering, budget, settlement, or guaranteed-arb
controls are changed here.
"""
from __future__ import annotations

import asyncio
import logging

from src import gemini_kalshi_basis_risk_q1_child_v6 as child_v6
from src import gemini_kalshi_basis_risk_rollover_supervisor_v6 as v6

logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_rollover_supervisor_v7")

GLOBAL_INCIDENT_REASON = "INFLIGHT_OR_UNPAIRED_EXECUTION_RISK"


async def _guarded_asset_loop(asset: str, lease_minutes: int) -> None:
    """Run one v6 asset loop and escalate unresolved execution incidents globally."""
    await v6._asset_loop(asset, lease_minutes)
    state = v6._state()
    allowed, reason = child_v6.automatic_rollover_allowed(state)
    if not allowed and reason == GLOBAL_INCIDENT_REASON:
        v6._log_state("CRYPTO_BASIS_RISK_V7_GLOBAL_INCIDENT_STOP", state, reason, asset=asset)
        raise RuntimeError(
            f"v7 global execution incident stop: asset={asset} reason={reason}"
        )


async def main_async() -> None:
    v6._validate_outer_authorization()
    lease_minutes = v6._lease_minutes()
    initial = v6._state()
    allowed, reason = child_v6.automatic_rollover_allowed(initial)
    v6._log_state("CRYPTO_BASIS_RISK_V7_BOOT", initial, reason)
    if not allowed and v6._globally_terminal(reason):
        return
    if not allowed and reason == GLOBAL_INCIDENT_REASON:
        raise RuntimeError(f"v7 refuses boot with unresolved execution incident: {reason}")

    logger.warning(
        "CRYPTO_BASIS_RISK_V7_GLOBAL_INCIDENT_CONTAINMENT_ENABLED assets=%s lease_minutes=%s",
        ",".join(v6.ASSETS),
        lease_minutes,
    )
    tasks = [
        asyncio.create_task(
            _guarded_asset_loop(asset, lease_minutes),
            name=f"basis-v7-{asset.lower()}",
        )
        for asset in v6.ASSETS
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

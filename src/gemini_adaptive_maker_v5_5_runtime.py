"""Restart-safe runtime for Gemini adaptive maker V5.5.

Recovery installation traverses legacy V5.2 code and mutates shared hooks, so V5.5
reasserts and verifies its exact campaign/FOK patches immediately before the live
loop. Existing durable open state still routes to recovery only; no new campaign is
started in that condition.
"""
from __future__ import annotations

import asyncio
import json

from src import gemini_adaptive_maker_contract_grid_v5_3 as grid
from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_executor_v5_5 as v55
from src import gemini_adaptive_maker_v5_3_recovery as recovery53
from src import gemini_q10_production_executor_v5_2 as v52


def install_new_campaign_runtime() -> None:
    recovery53.install()
    v55.install()
    if getattr(v53.v5, "_run_campaign", None) is not v55.run_campaign_v55:
        raise RuntimeError("V5.5 install integrity failure: campaign hook is not V5.5")
    if v52.bounded_fok_v52 is not v55.bounded_fok_v55:
        raise RuntimeError("V5.5 install integrity failure: bounded FOK patch is not V5.5")
    if v53._event_contract_ok is not grid.event_contract_ok:
        raise RuntimeError("V5.5 install integrity failure: contract-grid admission not installed")
    if v53.policy.select_plan is not grid.select_plan_grid_aware:
        raise RuntimeError("V5.5 install integrity failure: bounded grid planner not installed")


async def run() -> dict:
    install_new_campaign_runtime()
    report, _, _, _ = await recovery53.audit()
    if report.open_state:
        result = await recovery53.recover()
        print(
            "GEMINI_ADAPTIVE_MAKER_V5_5_RUNTIME="
            + json.dumps(
                {
                    "status": "RECOVERY_PATH_ONLY",
                    "recovery": result,
                    "new_campaign_started": False,
                },
                separators=(",", ":"),
                default=str,
            ),
            flush=True,
        )
        return result

    # Legacy recovery/audit can reinstall V5.2 shared hooks. Reassert V5.5 at the
    # final boundary immediately before production run.
    install_new_campaign_runtime()
    await v53.v5.run()
    result = {
        "status": "V5_5_RUN_RETURNED",
        "new_campaign_started": bool(v53.v5.live_armed()),
        "campaign_hook": "gemini_adaptive_maker_executor_v5_5.run_campaign_v55",
        "fok_hook": "gemini_adaptive_maker_executor_v5_5.bounded_fok_v55",
        "unique_continuous_client_ids": True,
        "contract_grid_installed": True,
        "catastrophic_campaign_bound_installed": True,
        "persistent_daily_risk_installed": True,
    }
    print(
        "GEMINI_ADAPTIVE_MAKER_V5_5_RUNTIME="
        + json.dumps(result, separators=(",", ":"), default=str),
        flush=True,
    )
    return result


if __name__ == "__main__":
    asyncio.run(run())

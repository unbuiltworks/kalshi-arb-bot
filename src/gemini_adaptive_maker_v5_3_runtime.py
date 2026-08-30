"""Restart-safe runtime for Gemini BTC5 adaptive maker V5.4 successor release.

The frozen V5.3 canary exposed an installation-order bug: recovery installation
called V5.2 installation after V5.3 and silently replaced the shared campaign hook.
V5.4 installs recovery first, then reasserts the V5.4 campaign wrapper immediately
before the production run loop. The wrapper includes contract-grid admission, the
catastrophic $1 campaign-loss bound, and persistent account-level daily risk.
"""
from __future__ import annotations

import asyncio
import json

from src import gemini_adaptive_maker_contract_grid_v5_3 as grid
from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_executor_v5_4 as v54
from src import gemini_adaptive_maker_v5_3_recovery as recovery53


def install_new_campaign_runtime() -> None:
    """Install recovery wiring first, then reassert the exact V5.4 live path."""
    recovery53.install()
    v54.install()
    if getattr(v53.v5, "_run_campaign", None) is not v54.run_campaign_v54:
        raise RuntimeError("V5.4 install integrity failure: campaign hook is not V5.4")
    if v53._event_contract_ok is not grid.event_contract_ok:
        raise RuntimeError("V5.4 install integrity failure: contract-grid admission not installed")
    if v53.policy.select_plan is not grid.select_plan_grid_aware:
        raise RuntimeError("V5.4 install integrity failure: bounded grid planner not installed")


async def run() -> dict:
    install_new_campaign_runtime()
    report, _, _, _ = await recovery53.audit()
    if report.open_state:
        # Recovery is the only permitted action when durable open state exists.
        result = await recovery53.recover()
        print(
            "GEMINI_ADAPTIVE_MAKER_V5_4_RUNTIME="
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

    # recovery53.audit() traverses legacy V5.2 audit code that calls v52.install().
    # Restore the exact V5.4 hook after that side effect and immediately before run.
    install_new_campaign_runtime()
    await v53.v5.run()
    result = {
        "status": "V5_4_RUN_RETURNED",
        "new_campaign_started": bool(v53.v5.live_armed()),
        "campaign_hook": "gemini_adaptive_maker_executor_v5_4.run_campaign_v54",
        "contract_grid_installed": True,
        "catastrophic_campaign_bound_installed": True,
        "persistent_daily_risk_installed": True,
    }
    print(
        "GEMINI_ADAPTIVE_MAKER_V5_4_RUNTIME="
        + json.dumps(result, separators=(",", ":"), default=str),
        flush=True,
    )
    return result


if __name__ == "__main__":
    asyncio.run(run())

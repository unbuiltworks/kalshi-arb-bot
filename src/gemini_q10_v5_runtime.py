"""Production runtime gate for Gemini q10 V5.

Fresh starts run V5 normally. A restart that finds unresolved durable state never
starts a new campaign. It first audits recovery state and only performs recovery
when the independent recovery arm is present; after recovery the process exits so a
new live campaign always requires a fresh start/authorization boundary.
"""
from __future__ import annotations

import asyncio
import json

from src import gemini_q10_production_executor_v5 as v5
from src import gemini_q10_v5_recovery as recovery


async def run() -> dict:
    report, state, _, _ = await recovery.audit()
    if report.open_state:
        result = await recovery.recover()
        print("GEMINI_Q10_V5_RUNTIME=" + json.dumps({
            "status":"RECOVERY_PATH_ONLY",
            "recovery":result,
            "new_campaign_started":False,
        }, separators=(",", ":"), default=str), flush=True)
        return result

    # Disarmed mode remains read-only because V5 itself enforces the dual-key arm.
    await v5.run()
    result = {"status":"V5_RUN_RETURNED", "new_campaign_started":bool(v5.live_armed())}
    print("GEMINI_Q10_V5_RUNTIME=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
    return result


if __name__ == "__main__":
    asyncio.run(run())

"""Restart-safe production runtime for Gemini BTC5 q10 V5.2."""
from __future__ import annotations

import asyncio
import json

from src import gemini_q10_production_executor_v5_2 as v52
from src import gemini_q10_v5_2_recovery as recovery52


async def run() -> dict:
    v52.install()
    recovery52.install()
    report, state, _, _ = await recovery52.audit()
    if report.open_state:
        result = await recovery52.recover()
        print(
            "GEMINI_Q10_V5_2_RUNTIME=" + json.dumps({
                "status": "RECOVERY_PATH_ONLY",
                "recovery": result,
                "new_campaign_started": False,
            }, separators=(",", ":"), default=str),
            flush=True,
        )
        return result

    await v52.v5.run()
    result = {
        "status": "V5_2_RUN_RETURNED",
        "new_campaign_started": bool(v52.v5.live_armed()),
    }
    print(
        "GEMINI_Q10_V5_2_RUNTIME=" + json.dumps(result, separators=(",", ":"), default=str),
        flush=True,
    )
    return result


if __name__ == "__main__":
    asyncio.run(run())

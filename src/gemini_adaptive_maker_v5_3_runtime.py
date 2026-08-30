"""Restart-safe runtime for Gemini BTC5 adaptive maker V5.3."""
from __future__ import annotations

import asyncio
import json

from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_v5_3_recovery as recovery53


async def run() -> dict:
    v53.install()
    recovery53.install()
    report, _, _, _ = await recovery53.audit()
    if report.open_state:
        result = await recovery53.recover()
        print(
            "GEMINI_ADAPTIVE_MAKER_V5_3_RUNTIME="
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

    await v53.v5.run()
    result = {
        "status": "V5_3_RUN_RETURNED",
        "new_campaign_started": bool(v53.v5.live_armed()),
    }
    print(
        "GEMINI_ADAPTIVE_MAKER_V5_3_RUNTIME="
        + json.dumps(result, separators=(",", ":"), default=str),
        flush=True,
    )
    return result


if __name__ == "__main__":
    asyncio.run(run())

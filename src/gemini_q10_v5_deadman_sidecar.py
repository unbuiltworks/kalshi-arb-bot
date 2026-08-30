"""Long-lived process wrapper for the V5 dead-man.

Designed to run in a separate container/process from the executor. It waits for an
open durable campaign, then checks heartbeat/recovery continuously. The deployment
supervisor stops this sidecar after the executor has exited and post-run account
reconciliation is complete.
"""
from __future__ import annotations

import asyncio
import json

from src import gemini_q10_production_executor_v5 as v5
from src import gemini_q10_v5_deadman as deadman
from src import gemini_q10_v5_recovery as recovery


async def tick() -> dict:
    state = v5.v1._read_state()
    if not recovery._is_open_state(state):
        return {"status":"WAITING_FOR_OPEN_CAMPAIGN", "intervened":False}
    return await deadman.check_once()


async def run() -> None:
    while True:
        result = await tick()
        print("GEMINI_Q10_V5_DEADMAN_SIDECAR=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
        if result.get("intervened"):
            return
        await asyncio.sleep(deadman.poll_seconds())


if __name__ == "__main__":
    asyncio.run(run())

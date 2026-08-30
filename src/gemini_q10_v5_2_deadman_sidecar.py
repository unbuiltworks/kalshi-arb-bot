"""Rate-limited long-lived sidecar wrapper for the V5.2 dead-man."""
from __future__ import annotations

import asyncio
import json

from src import gemini_q10_production_executor_v5_2 as v52
from src import gemini_q10_v5_2_deadman as deadman
from src import gemini_q10_v5_2_recovery as recovery52


async def tick() -> dict:
    v52.install()
    recovery52.install()
    state = v52.v5.v1._read_state()
    if not recovery52.recovery._is_open_state(state):
        return {"status": "WAITING_FOR_OPEN_CAMPAIGN", "intervened": False}
    return await deadman.check_once()


async def run() -> None:
    loop = asyncio.get_running_loop()
    last_status = None
    last_print = 0.0
    while True:
        try:
            result = await tick()
        except Exception as exc:
            result = {
                "status": "WATCHDOG_CHECK_ERROR",
                "intervened": False,
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:240],
            }
        now = loop.time()
        status = result.get("status")
        if status != last_status or now - last_print >= 30.0:
            print(
                "GEMINI_Q10_V5_2_DEADMAN_SIDECAR="
                + json.dumps(result, separators=(",", ":"), default=str),
                flush=True,
            )
            last_status = status
            last_print = now
        if result.get("intervened") or status == "STALE_HEARTBEAT_NO_DIRECTIONAL_RESIDUAL":
            return
        await asyncio.sleep(deadman.poll_seconds())


if __name__ == "__main__":
    asyncio.run(run())

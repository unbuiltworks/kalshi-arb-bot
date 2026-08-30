"""Rate-limited sidecar wrapper for Gemini adaptive maker V5.3 dead-man."""
from __future__ import annotations

import asyncio
import json

from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_v5_3_deadman as deadman
from src import gemini_adaptive_maker_v5_3_recovery as recovery53


async def tick() -> dict:
    v53.install()
    recovery53.install()
    state = v53.v5.v1._read_state()
    if not recovery53.recovery52.recovery._is_open_state(state):
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
                "GEMINI_ADAPTIVE_MAKER_V5_3_DEADMAN_SIDECAR="
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

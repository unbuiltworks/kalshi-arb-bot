"""Long-lived V5.6 watchdog sidecar.

Unlike the one-shot deadman module, this process intentionally stays alive while the
executor is between quotes/windows. The workflow owns its lifetime and removes it
when the bounded live session ends.
"""
from __future__ import annotations

import asyncio
import json

from src import gemini_selective_maker_v5_6_deadman as deadman


async def run() -> None:
    last_status = None
    last_print = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            result = await deadman.check_once()
        except Exception as exc:
            result = {"status": "WATCHDOG_CHECK_ERROR", "intervened": False,
                      "error_type": type(exc).__name__, "error_message": str(exc)[:240]}
        now = loop.time()
        status = result.get("status")
        if status != last_status or now - last_print >= 30.0:
            print("GEMINI_SELECTIVE_MAKER_V5_6_SIDECAR=" +
                  json.dumps(result, separators=(",", ":"), default=str), flush=True)
            last_status, last_print = status, now
        await asyncio.sleep(deadman.poll_seconds())


if __name__ == "__main__":
    asyncio.run(run())

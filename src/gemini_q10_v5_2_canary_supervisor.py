"""One-shot Railway supervisor for the bounded Gemini q10 V5.2 canary.

Runs the live executor and dead-man watchdog as separate child processes. If the
executor exits non-zero, the supervisor deliberately leaves the watchdog alive for
20 seconds so the 15-second stale-heartbeat threshold can trigger bounded recovery.
"""
from __future__ import annotations

import asyncio
import sys


async def _stop(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


async def run() -> int:
    watchdog = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-m",
        "src.gemini_q10_v5_2_deadman_sidecar",
    )
    executor = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-m",
        "src.gemini_q10_v5_2_runtime",
    )
    try:
        rc = await executor.wait()
        if rc != 0:
            await asyncio.sleep(20.0)
        return rc
    finally:
        await _stop(watchdog)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))

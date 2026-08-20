"""Short read-only fanout test for four pinned crypto scanner children.

Each child runs the live canary market-data/readiness stack but cannot install the
persistent live arm and has the live-order environment gate forced off.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

ASSETS = ("BTC", "ETH", "SOL", "XRP")
RUN_SECONDS = float(os.getenv("CRYPTO_PARALLEL_SHADOW_SECONDS", "20"))


async def run() -> None:
    stamp = int(time.time())
    processes = {}
    for asset in ASSETS:
        env = os.environ.copy()
        env["CRYPTO_CANARY_ASSET"] = asset
        env["CRYPTO_PARALLEL_SHADOW_CHILD_SECONDS"] = str(max(RUN_SECONDS + 10, 30))
        env["ENABLE_GEMINI_KALSHI_LIVE_PROBES"] = "NO"
        p = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m", "src.gemini_kalshi_parallel_shadow_child",
            env=env,
        )
        processes[asset] = p
    await asyncio.sleep(RUN_SECONDS)
    alive = {asset: p.returncode is None for asset, p in processes.items()}
    for p in processes.values():
        if p.returncode is None:
            p.terminate()
    results = {}
    for asset, p in processes.items():
        try:
            code = await asyncio.wait_for(p.wait(), timeout=5)
        except asyncio.TimeoutError:
            p.kill()
            code = await p.wait()
        results[asset] = code
    print(
        "CRYPTO_PARALLEL_SHADOW_FANOUT_RESULT " + json.dumps(
            {
                "mode": "READ_ONLY_FANOUT",
                "stamp": stamp,
                "run_seconds": RUN_SECONDS,
                "alive_through_observation": alive,
                "exit_codes_after_forced_stop": results,
                "all_four_alive": all(alive.values()),
                "live_order_env_forced": "NO",
                "order_entry": False,
            },
            separators=(",", ":"), sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(run())

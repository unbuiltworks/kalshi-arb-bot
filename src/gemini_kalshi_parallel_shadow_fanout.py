"""Short read-only fanout test for four pinned crypto scanner children.

The live-order environment gate is forcibly disabled in every child. This validates
transport load, asset pinning, rollover readiness and gate telemetry only.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import sys
import time

ASSETS = ("BTC", "ETH", "SOL", "XRP")
RUN_SECONDS = float(os.getenv("CRYPTO_PARALLEL_SHADOW_SECONDS", "20"))


async def run() -> None:
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    stamp = int(time.time())
    processes = {}
    for asset in ASSETS:
        env = os.environ.copy()
        env["CRYPTO_CANARY_ASSET"] = asset
        env["SOL_CANARY_V2_RUN_ID"] = f"shadowfan-{stamp}-{asset.lower()}"
        env["SOL_CANARY_V2_ARM"] = "YES"
        env["SOL_CANARY_V2_ARM_NOT_AFTER_UTC"] = deadline
        env["ENABLE_GEMINI_KALSHI_LIVE_PROBES"] = "NO"
        env["LIVE_PROBE_ARM_TOKEN_FILE"] = f"/tmp/shadowfan-{stamp}-{asset.lower()}.arm"
        p = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m", "src.gemini_kalshi_crypto_campaign_child_reliable",
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

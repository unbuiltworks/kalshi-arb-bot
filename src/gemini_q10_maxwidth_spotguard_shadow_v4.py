"""Read-only BTC5 q10 reward shadow V4.

Runs the V3 diagnostics with the shadow-only heartbeat-aware Coinbase guard so quiet
trade flow does not cause false stale-feed pull/repost churn. Live execution remains
unchanged.
"""
from __future__ import annotations

import asyncio

from src import gemini_q10_maxwidth_spotguard_shadow as base
from src import gemini_q10_maxwidth_spotguard_shadow_v3 as v3
from src.btc_spot_toxicity_guard_v2_shadow import BtcSpotGuardV2Shadow


async def run() -> None:
    original = base.BtcSpotGuard
    base.BtcSpotGuard = BtcSpotGuardV2Shadow
    base.emit(
        "V4_HEARTBEAT_LIVENESS_ACTIVE",
        live_executor_unchanged=True,
        read_only=True,
        order_entry=False,
    )
    try:
        await v3.run()
    finally:
        base.BtcSpotGuard = original


if __name__ == "__main__":
    asyncio.run(run())

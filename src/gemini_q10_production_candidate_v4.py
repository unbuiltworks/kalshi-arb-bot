"""Q10 production candidate V4: V3 spot protection + persistent prediction depth.

This wrapper removes connection setup from every prediction-book decision. It
lazily opens one public `{symbol}@depth5@100ms` feed when the candidate selector
first needs a book and reuses that feed through preflight and dynamic completion.
V3/V2 remain responsible for the live arm, durable one-use state, spot-toxicity
guard, and bounded maker-only rescue.
"""
from __future__ import annotations

import asyncio

from src import gemini_q10_production_candidate as v1
from src import gemini_q10_production_candidate_v3 as v3
from src.gemini_q10_prediction_depth_ws import DepthManager


async def run() -> None:
    if not v1.live_armed():
        await v3.run()
        return

    manager = DepthManager()
    original = v1.base.fresh_top

    async def persistent_top(symbol: str, timeout: float = 4.0):
        return await manager.top(symbol, timeout=timeout)

    v1.base.fresh_top = persistent_top
    try:
        await v3.run()
    finally:
        v1.base.fresh_top = original
        await manager.close()


if __name__ == "__main__":
    asyncio.run(run())

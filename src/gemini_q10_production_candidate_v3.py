"""Final q10 production-candidate wrapper: V2 safety + low-latency BTC spot feed.

The v1 executor's pre-fill toxicity loop is retained, but its 200ms REST ticker
read is replaced by a continuously updated public `BTCUSD@bookTicker` WebSocket
midpoint. This removes repeated REST round trips from the hot protection path.
No live mutation can occur unless v1's existing dual-key arm is satisfied, and V2's
durable one-use state remains authoritative.
"""
from __future__ import annotations

import asyncio

from src import gemini_q10_production_candidate as v1
from src import gemini_q10_production_candidate_v2 as v2
from src.gemini_q10_spot_ws_guard import GeminiSpotBookTicker


async def run() -> None:
    # Preserve strict disarmed behavior: do not even open a public socket when not armed.
    if not v1.live_armed():
        await v2.run()
        return

    feed = GeminiSpotBookTicker()
    await feed.start(startup_timeout=3.0)
    original_spot = v1._spot

    async def websocket_spot(_http_client):
        return feed.latest()

    v1._spot = websocket_spot
    try:
        await v2.run()
    finally:
        v1._spot = original_spot
        await feed.close()


if __name__ == "__main__":
    asyncio.run(run())

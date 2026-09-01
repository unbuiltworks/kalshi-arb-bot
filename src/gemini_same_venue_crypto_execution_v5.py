"""Same-Gemini q=1 execution v5: v4 coverage + maker activity diagnostics.

No live maker admission change. This wrapper only swaps the flow selector for v3,
which reports event-wide recent activity for suppressed maker candidates while
keeping the exact-contract flow gate unchanged.
"""
from __future__ import annotations

from src import gemini_same_venue_crypto_execution_v4 as v4
from src import gemini_same_venue_crypto_execution_v3 as v3
from src import gemini_same_venue_crypto_flow_v3 as flow_v3


async def run():
    v3.flow.discover_flow_ranked_v2 = flow_v3.discover_flow_ranked_v3
    return await v4.run()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run())

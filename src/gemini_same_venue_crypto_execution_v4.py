"""Same-Gemini q=1 execution v4: complete threshold-pair universe.

Keeps v3's account-authoritative recovery, continuous taker-first loop, unique maker
IDs, 100ms depth, flow-qualified sticky maker, exact fees, and >=3c floor. The only
change is opportunity coverage: maker and taker discovery evaluate every certified
same-event/source/expiry L<H pair rather than adjacent thresholds only.
"""
from __future__ import annotations

from src import gemini_same_venue_crypto_execution_v3 as v3
from src import gemini_same_venue_crypto_full_pair_taker as full_taker


async def run():
    # Hardened taker transport delegates discovery through its base module.
    v3.taker.base.discover = full_taker.discover
    return await v3.run()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run())

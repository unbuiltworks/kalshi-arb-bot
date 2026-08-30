"""Read-only V5.1 shadow: exact V5 risk path with refined admission."""
from __future__ import annotations

import asyncio

from src import gemini_q10_production_executor_v5_1 as v51
from src import gemini_q10_production_shadow_v5 as shadow


async def run() -> None:
    v51.install()
    await shadow.run()


if __name__ == "__main__":
    asyncio.run(run())

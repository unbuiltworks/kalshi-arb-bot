"""Restart-safe production runtime for V5.1 admission + V5 rescue."""
from __future__ import annotations

import asyncio

from src import gemini_q10_production_executor_v5_1 as v51
from src import gemini_q10_v5_runtime as runtime


async def run():
    v51.install()
    return await runtime.run()


if __name__ == "__main__":
    asyncio.run(run())

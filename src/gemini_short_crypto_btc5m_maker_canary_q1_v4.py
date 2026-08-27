"""One-shot Gemini BTC5m q=1 canary using AWS-local durable accounting fallback.

This entrypoint is only for the Neon/Command-Center outage path. It preserves the
v3 execution/cancel/fractional-flatten logic and replaces only the budget backend
with an AWS-local fsynced journal plus direct Gemini account exposure gate.
"""
from __future__ import annotations

import asyncio

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src import gemini_short_crypto_btc5m_maker_canary_q1_v3 as v3
from src import gemini_short_crypto_local_budget as local_budget


async def run() -> None:
    base.budget.reserve = local_budget.reserve
    base.budget.release = local_budget.release
    base.budget.result = local_budget.result
    await v3.run()


if __name__ == "__main__":
    asyncio.run(run())

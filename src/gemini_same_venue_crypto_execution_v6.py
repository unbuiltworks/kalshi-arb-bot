"""Same-Gemini execution v6: v5 live logic plus read-only fractional-maker census.

The census runs once at process startup and cannot submit orders. Live admission is
unchanged: resting maker execution remains whole-contract-only; FOK/FOK remains all
q1-compatible certified pairs. Census output is evidence for a future narrow maker
expansion only if every possible partial fill size survives the locked-profit floor.
"""
from __future__ import annotations

from src import gemini_same_venue_crypto_execution_v5 as v5
from src import gemini_same_venue_crypto_fractional_maker_census as census


async def run():
    try:
        await census.run()
    except Exception as exc:
        # Census is diagnostic only. A diagnostic failure must not weaken or alter the
        # already-hardened live v5 execution path.
        print(f"GEMINI_FRACTIONAL_MAKER_CENSUS_ERROR type={type(exc).__name__} error={str(exc)[:180]}", flush=True)
    return await v5.run()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run())

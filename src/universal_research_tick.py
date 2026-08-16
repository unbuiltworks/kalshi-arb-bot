"""One read-only research tick for the universal prediction-market quant.

Each lane is isolated: a failure in one public-data inventory is logged without
silently converting another lane into a success. There is no authentication and no
order-entry code in this orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from src import sports_universe_inventory
from src import universal_three_venue_crypto

logger = logging.getLogger("kalshi_arb.universal_research_tick")


async def _run_lane(name: str, call: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    try:
        result = await call()
        return {"status": "ok", "result": result}
    except Exception as exc:
        logger.exception("UNIVERSAL_LANE_FAILED %s", name)
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc) or repr(exc),
        }


async def run_once() -> dict[str, Any]:
    crypto, sports = await asyncio.gather(
        _run_lane(
            "three_venue_crypto",
            lambda: universal_three_venue_crypto.run_once(
                max_window_skew_s=30.0,
                max_poly_window_skew_s=120.0,
            ),
        ),
        _run_lane("sports_universe_inventory", sports_universe_inventory.run_once),
    )
    summary = {
        "lanes": {
            "three_venue_crypto": crypto,
            "sports_universe_inventory": sports,
        },
        "read_only": True,
        "order_entry": False,
    }
    logger.info("UNIVERSAL_RESEARCH_TICK %s", json.dumps(summary, separators=(",", ":")))
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()

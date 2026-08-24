"""Production combo supervisor v35: read-only probe of Kalshi MVE leg-count capability.

The live generator historically hard-capped baskets at 2-5 legs even when a
collection's advertised size_max may be larger.  This layer performs one
read-only collection metadata probe at process start and logs observed size_min /
size_max values for sports multivariate collections.  It does not alter candidate
generation, market creation, RFQs, execution, thresholds, or live sizing.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from typing import Any

from src import sports_kp_live_combo_supervisor_v34 as v34
from src import sports_kp_mve_combo_generator as g
from src.auth import KalshiClient

logger = v34.v33.v32.v31.logger


async def _probe_leg_limits() -> None:
    client = KalshiClient(base_url=g.BASE)
    try:
        rows = [c for c in await g._open_collections(client)
                if g._sportsish(c.get("collection_ticker"), c.get("title"), c.get("description"), c.get("series_ticker"))]
        maxima = Counter()
        minima = Counter()
        above_five: list[dict[str, Any]] = []
        for c in rows:
            try:
                lo = int(c.get("size_min") or 2)
                hi = int(c.get("size_max") or 5)
            except Exception:
                continue
            minima[lo] += 1
            maxima[hi] += 1
            if hi > 5:
                above_five.append({
                    "collection_ticker": c.get("collection_ticker"),
                    "title": c.get("title"),
                    "size_min": lo,
                    "size_max": hi,
                    "associated_event_count": len(c.get("associated_events") or c.get("associated_event_tickers") or []),
                })
        logger.warning("SPORTS_MVE_V35_LEG_LIMIT_PROBE %s", json.dumps({
            "sports_collections_seen": len(rows),
            "size_min_distribution": dict(sorted(minima.items())),
            "size_max_distribution": dict(sorted(maxima.items())),
            "collections_above_five": len(above_five),
            "examples_above_five": above_five[:12],
            "historical_generator_cap": 5,
            "probe_only": True,
            "market_creation": False,
            "rfq": False,
            "order_entry": False,
            "live_execution_changed": False,
        }, separators=(",", ":"), default=str))
    except Exception as exc:
        logger.warning("SPORTS_MVE_V35_LEG_LIMIT_PROBE_ERROR %s", json.dumps({
            "error": type(exc).__name__, "detail": str(exc)[:180], "order_entry": False,
        }, separators=(",", ":")))
    finally:
        await client.close()


async def run() -> None:
    await _probe_leg_limits()
    await v34.run()


def main() -> None:
    import logging
    import os
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()

"""Rate-limit-safe read-only combo shadow runner."""
from __future__ import annotations

import asyncio
import json
import logging
import os

from src.sports_kp_combo_discovery_safe import discover_combo_candidates_safe

logger = logging.getLogger("kalshi_arb.sports_kp_combo_shadow_v2")


async def run() -> None:
    logger.info("SPORTS_KP_COMBO_SHADOW_START %s", json.dumps({
        "read_only": True,
        "order_entry": False,
        "supports_underlyings": [2, 3],
        "state_counts": {"2": 4, "3": 8},
        "live_gate_present": os.getenv("ENABLE_SPORTS_KP_COMBO_LIVE", "NO").strip().upper() == "YES",
        "live_gate_effective": False,
    }, separators=(",", ":")))
    result = await discover_combo_candidates_safe()
    logger.info("SPORTS_KP_COMBO_DISCOVERY %s", json.dumps(result, separators=(",", ":"), default=str))


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()

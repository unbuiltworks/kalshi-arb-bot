"""Read-only Kalshi/Polymarket combo/synthetic arbitrage shadow service.

V1 responsibilities:
* discover current combo-looking markets on both venues,
* map only explicit structured 2-3 leg definitions,
* keep text-only candidates research-only,
* expose exact state-space solver readiness,
* never place orders.

The live q=1 executor is intentionally not imported here.

Deployment note: the production combo service now starts the separate
sports_kp_live_combo_supervisor; this module remains the read-only fallback.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os

from src.sports_kp_combo_discovery import discover_combo_candidates

logger = logging.getLogger("kalshi_arb.sports_kp_combo_shadow")


async def run(interval_seconds: float) -> None:
    logger.info("SPORTS_KP_COMBO_SHADOW_START %s", json.dumps({
        "read_only": True,
        "order_entry": False,
        "supports_underlyings": [2, 3],
        "state_counts": {"2": 4, "3": 8},
        "live_gate_present": os.getenv("ENABLE_SPORTS_KP_COMBO_LIVE", "NO").strip().upper() == "YES",
        "live_gate_effective": False,
        "status_lifecycle": ["SHADOW", "EXECUTABLE", "SUBMITTED", "FILLED", "SETTLED", "REALIZED"],
    }, separators=(",", ":")))
    while True:
        try:
            result = await discover_combo_candidates()
            # Keep logs bounded: counts plus mapped rows. Text-only inventory can be
            # large and is not actionable until its native payload exposes structure.
            logger.info("SPORTS_KP_COMBO_DISCOVERY %s", json.dumps({
                "observed_at": result["observed_at"],
                "candidates": result["candidates"],
                "mapped_structured": result["mapped_structured"],
                "unmapped_text_only": result["unmapped_text_only"],
                "by_venue": result["by_venue"],
                "mapped": result["mapped"][:100],
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
        except Exception as exc:
            logger.exception("SPORTS_KP_COMBO_DISCOVERY_ERROR %s", json.dumps({
                "type": type(exc).__name__, "message": str(exc)[:300], "order_entry": False,
            }, separators=(",", ":")))
        await asyncio.sleep(interval_seconds)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--interval-seconds", type=float, default=60.0)
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    args = parse_args()
    raise SystemExit(asyncio.run(run(max(5.0, args.interval_seconds))))


if __name__ == "__main__":
    main()

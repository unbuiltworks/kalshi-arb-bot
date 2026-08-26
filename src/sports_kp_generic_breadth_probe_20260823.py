"""One-off read-only breadth probe for sports throughput prioritization.

Inventories current Polymarket US sports market families and runs the strict MLB
prop paper-economics scanner. No order-entry code is imported or enabled here.
"""
from __future__ import annotations

import asyncio
import json
import logging

from src.sports_kp_live_event_inventory import run_once as run_inventory
from src.strict_mlb_prop_snapshot_v5 import run_once as run_mlb_props

logger = logging.getLogger("kalshi_arb.sports_kp_generic_breadth_probe_20260823")


async def main_async() -> None:
    inventory = await run_inventory()
    props = await run_mlb_props()
    summary = {
        "inventory": {
            "events": inventory.get("events"),
            "markets": inventory.get("markets"),
            "started_or_live_by_start_time": inventory.get("started_or_live_by_start_time"),
            "leagues": inventory.get("leagues"),
            "market_types": inventory.get("market_types"),
        },
        "mlb_props": {
            "total_strict_candidates": props.get("total_strict_candidates"),
            "participation_cleared_candidates": props.get("participation_cleared_candidates"),
            "batch_size": props.get("batch_size"),
            "book_comparable": props.get("book_comparable"),
            "probe_1usd_positive_pairs": props.get("probe_1usd_positive_pairs"),
            "probe_5usd_positive_pairs": props.get("probe_5usd_positive_pairs"),
            "positive_paper_probe_pairs": props.get("positive_paper_probe_pairs"),
            "preferred_paper_probe_pairs": props.get("preferred_paper_probe_pairs"),
            "rules_status": props.get("rules_status"),
        },
        "read_only": True,
        "order_entry": False,
    }
    encoded = json.dumps(summary, separators=(",", ":"), default=str)
    logger.warning("SPORTS_BREADTH_THROUGHPUT_PROBE %s", encoded)
    print("SPORTS_BREADTH_THROUGHPUT_PROBE " + encoded, flush=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

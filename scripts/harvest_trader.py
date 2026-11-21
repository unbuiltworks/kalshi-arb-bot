#!/usr/bin/env python3
"""Read-only harvest of one Polymarket wallet into data/traders/<name>/raw.

Usage: python3 scripts/harvest_trader.py <name> <0xaddress> [--pages N]
Public GET only; no credentials. Same recipe validated on the L5 study:
windowed DESC activity walk (past the offset cap), oldest ASC slice,
taker-only walk for maker/taker labeling, positions, closed-market metadata.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.polymarket_l5.cache import RawCache  # noqa: E402
from src.polymarket_l5.fetch import (  # noqa: E402
    fetch_activity, fetch_activity_windowed, fetch_markets_by_condition, fetch_positions,
)


def harvest(name: str, address: str, pages: int) -> None:
    cache = RawCache(Path("data/traders") / name / "raw")
    print(f"=== {name} {address} ===", flush=True)
    print("ACTIVITY_DESC", json.dumps(
        fetch_activity_windowed(address, cache=cache, max_total_pages=pages)), flush=True)
    print("ACTIVITY_ASC", json.dumps(fetch_activity(address, cache=cache)), flush=True)
    print("TAKERONLY", json.dumps(
        fetch_activity_windowed(address, cache=cache, endpoint="trades", tag="trades_takeronly",
                                extra_params={"takerOnly": "true"},
                                max_total_pages=max(20, pages // 2))), flush=True)
    fetch_positions(address, cache=cache)
    ids = []
    for tag in ("activity", "positions_all", "positions_redeemable", "positions_closed"):
        for batch in cache.payload_batches(tag):
            for item in batch:
                cid = item.get("conditionId")
                if cid:
                    ids.append(cid)
    print("unique condition ids:", len(dict.fromkeys(ids)), flush=True)
    print("MARKETS_DONE", json.dumps(fetch_markets_by_condition(ids, cache=cache)), flush=True)
    print(f"HARVEST_COMPLETE {name}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("address")
    parser.add_argument("--pages", type=int, default=100)
    args = parser.parse_args()
    harvest(args.name, args.address.lower(), args.pages)


if __name__ == "__main__":
    main()

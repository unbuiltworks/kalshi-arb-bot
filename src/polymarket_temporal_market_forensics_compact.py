"""Compact read-only runner for lot-level temporal market forensics."""
from __future__ import annotations

import argparse
import json
import time

import httpx

from src.polymarket_temporal_market_forensics import analyze_condition
from src.polymarket_wallet_shadow import TRADERS, fetch_activity_window


def main() -> None:
    parser = argparse.ArgumentParser(description="Compact Polymarket temporal market forensics")
    parser.add_argument("--condition-id", action="append", required=True)
    parser.add_argument("--trader", choices=sorted(TRADERS), default="l5")
    parser.add_argument("--history-hours", type=float, default=2.0)
    args = parser.parse_args()

    trader = TRADERS[args.trader]
    end = int(time.time())
    start = end - max(60, int(max(0.01, args.history_hours) * 3600))
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "arb-temporal-forensics/1.0"}) as http:
        rows = fetch_activity_window(http, trader, start=start, end=end)

    for condition_id in args.condition_id:
        out = analyze_condition(rows, condition_id)
        out.pop("pair_examples", None)
        out["type"] = "temporal_market_forensics_compact"
        print(json.dumps(out, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

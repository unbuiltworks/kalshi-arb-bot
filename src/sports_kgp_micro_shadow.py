"""Read-only K/G/P micro-live approval and market-identity join shadow.

This process never imports an order transport. It proves that the unified
micro-live approval registry can join exact currently discoverable MLB contracts
across Kalshi, Gemini and Polymarket before any execution coordinator is armed.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from itertools import combinations
import json
from pathlib import Path
from typing import Any

import httpx

from src.sports_kgp_micro_approvals import MicroApproval, load_micro_approvals
from src.sports_kgp_micro_policy import MicroLivePolicy, PREDICTION_VENUES, prediction_pair_only
from src.three_venue_mlb_hot import HotUniverse, discover_hot_universe


def _pair_name(venues: tuple[str, str]) -> str:
    return "+".join(sorted(venues))


def _pair_inventory(universe: HotUniverse) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for hot in universe.contracts.values():
        venues = sorted(set(hot.sides).intersection(PREDICTION_VENUES))
        for a, b in combinations(venues, 2):
            pair = (a, b)
            if prediction_pair_only(pair):
                counts[_pair_name(pair)] += 1
    return dict(sorted(counts.items()))


def _join_approval(universe: HotUniverse, approval: MicroApproval) -> tuple[bool, str]:
    hot = universe.contracts.get(approval.settlement_key)
    if hot is None:
        return False, "settlement_key_not_in_hot_universe"
    for venue in approval.venues:
        side = hot.sides.get(venue)
        if side is None:
            return False, f"venue_missing:{venue}"
        if side.venue_market_id != approval.market_ids[venue]:
            return False, f"market_id_changed:{venue}"
        if str(side.side).upper() != approval.canonical_sides[venue]:
            return False, f"canonical_side_changed:{venue}"
    return True, "joined"


async def run_once(approvals_path: Path) -> dict[str, Any]:
    approvals = load_micro_approvals(approvals_path)
    async with httpx.AsyncClient(timeout=30.0) as http:
        universe = await discover_hot_universe(http)

    joined: list[str] = []
    rejected: dict[str, str] = {}
    for approval_id, approval in approvals.items():
        ok, reason = _join_approval(universe, approval)
        if ok:
            joined.append(approval_id)
        else:
            rejected[approval_id] = reason

    policy = MicroLivePolicy()
    return {
        "mode": "SPORTS_KGP_MICRO_SHADOW",
        "hot_contracts": len(universe.contracts),
        "hot_pair_inventory": _pair_inventory(universe),
        "approved_pairs": len(approvals),
        "joined_execution_pairs": len(joined),
        "joined_ids": sorted(joined),
        "rejected_approvals": rejected,
        "micro_policy": {
            "prediction_markets_only": sorted(PREDICTION_VENUES),
            "max_contracts_per_leg": policy.max_contracts_per_leg,
            "max_combined_capital_usd": str(policy.max_combined_capital_usd),
            "min_after_fee_profit_per_contract": str(policy.min_after_fee_profit_per_contract),
            "max_quote_skew_ms": policy.max_quote_skew_ms,
            "max_book_age_ms": policy.max_book_age_ms,
            "min_persistence_ms": policy.min_persistence_ms,
            "max_attempts_per_arm": policy.max_attempts_per_arm,
        },
        "read_only": True,
        "order_transport_imported": False,
        "order_entry": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approvals", default="config/sports_kgp_micro_execution_approvals.json")
    args = parser.parse_args()
    result = asyncio.run(run_once(Path(args.approvals)))
    print("SPORTS_KGP_MICRO_SHADOW " + json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

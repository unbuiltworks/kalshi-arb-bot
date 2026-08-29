"""Compact read-only live Gemini incentive scan summary."""
from __future__ import annotations

import asyncio
import json

from src.gemini_dual_maker_incentive_scan import scan


def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


async def main() -> None:
    payload = await scan()
    rows = [r for r in payload.get("rows", []) if r.get("two_sided_reward_eligible_at_snapshot")]
    rows.sort(key=lambda r: (
        r.get("reported_qualifying_maker_count") if r.get("reported_qualifying_maker_count") is not None else 999999,
        -_num(r.get("trade_only_locked_edge_per_pair")),
        _num(r.get("seconds_to_expiry")),
    ))
    keys = [
        "ticker", "asset", "horizon", "seconds_to_expiry", "yes_bid", "yes_ask", "no_bid",
        "spread", "trade_only_locked_edge", "trade_only_locked_edge_per_pair",
        "projected_pair_rebate_if_both_filled", "projected_pair_edge_if_rebates_later_paid",
        "liquidity_reward_daily_pool_usd", "liquidity_reward_pool_event_count",
        "reported_qualifying_maker_count", "yes_distance_from_mid", "no_distance_from_mid",
    ]
    compact = {k: payload.get(k) for k in [
        "observed_at", "classification", "order_entry", "credentials_loaded",
        "active_crypto_rebate_rate", "liquidity_rewards_enabled", "reward_min_quantity",
        "reward_max_distance_from_mid", "markets", "trade_only_arb_markets",
        "two_sided_reward_eligible_markets_at_snapshot",
    ]}
    compact["top_candidates"] = [{k: row.get(k) for k in keys} for row in rows[:12]]
    print("GEMINI_INCENTIVE_LIVE_SCAN=" + json.dumps(compact, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())

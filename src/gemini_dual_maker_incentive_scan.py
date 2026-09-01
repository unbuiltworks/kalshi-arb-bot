"""Read-only Gemini dual-maker incentive/arb scanner.

For each currently active BTC/ETH 5m/15m event, model resting q=10 bids on BOTH
outcomes at the current best maker prices:
  YES maker bid = public YES bid
  NO maker bid  = 1 - public YES ask

If both fills occur, their raw complete-set discount equals the public spread. We
then subtract current Gemini maker fees. Maker-rebate estimates and liquidity
reward metadata are reported separately and never used to classify trade-only arb.

No credentials, balances, account endpoints, order entry, cancellation, or realized
P&L are used here.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from typing import Any

import httpx

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_l5_threshold_model import maker_rebate_estimate
from src.gemini_l5_threshold_shadow import book, discover
from src.incentive_arb_engine import evaluate_pair
from src.short_crypto_incentive_snapshot import fetch_snapshot

ONE = D("1")
Q_REWARD_MIN = D("10")
REWARD_MAX_DISTANCE = D("0.10")


def _active_crypto_rebate_rate(snapshot: dict[str, Any]) -> D:
    rules = snapshot.get("gemini", {}).get("maker_rebate_rates", {}).get("rate_rules", [])
    now = datetime.now(timezone.utc)
    candidates: list[tuple[datetime, D]] = []
    for row in rules:
        if str(row.get("category") or "").lower() != "crypto":
            continue
        try:
            start = datetime.fromisoformat(str(row["effective_from"]).replace("Z", "+00:00"))
        except Exception:
            continue
        end_raw = row.get("effective_to")
        if end_raw:
            try:
                end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
            except Exception:
                continue
            if now > end:
                continue
        if start <= now:
            candidates.append((start, D(str(row.get("rebate_multiplier_bps") or 0)) / D("10000")))
    if not candidates:
        return D("0")
    return max(candidates, key=lambda item: item[0])[1]


def _reward_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("event_ticker") or ""): row
        for row in snapshot.get("gemini", {}).get("short_crypto_reward_events", [])
        if row.get("event_ticker")
    }


async def scan(quantity: D = Q_REWARD_MIN) -> dict[str, Any]:
    quantity = D(str(quantity))
    if quantity < Q_REWARD_MIN:
        raise ValueError("quantity must be >= 10 for reward-eligible measurement")

    incentives = await fetch_snapshot()
    rebate_rate = _active_crypto_rebate_rate(incentives)
    reward_events = _reward_map(incentives)
    cfg = incentives.get("gemini", {}).get("liquidity_rewards_config", {})
    max_distance = D(str(cfg.get("max_spread_cents") or 10)) / D("100")

    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={"User-Agent":"arb-gemini-dual-maker-scan/1.0"}) as client:
        events = await discover(client)
        for event in events.values():
            try:
                yes_bid, yes_bid_qty, yes_ask, yes_ask_qty = await book(client, event.symbol)
            except Exception as exc:
                rows.append({
                    "ticker": event.ticker,
                    "symbol": event.symbol,
                    "error": type(exc).__name__,
                    "classification": "BOOK_ERROR",
                })
                continue

            no_bid = ONE - yes_ask
            mid = (yes_bid + yes_ask) / D("2")
            no_mid = ONE - mid
            yes_fee = gemini_predictions_maker_fee(yes_bid, quantity)
            no_fee = gemini_predictions_maker_fee(no_bid, quantity)
            pair = evaluate_pair(
                venue="GEMINI",
                yes_price=yes_bid,
                no_price=no_bid,
                quantity=quantity,
                yes_fee=yes_fee,
                no_fee=no_fee,
            )
            yes_raw_rebate, yes_rounded_rebate = maker_rebate_estimate(
                yes_bid, quantity, rebate_rate=rebate_rate
            )
            no_raw_rebate, no_rounded_rebate = maker_rebate_estimate(
                no_bid, quantity, rebate_rate=rebate_rate
            )
            reward = reward_events.get(event.ticker)
            yes_distance = mid - yes_bid
            no_distance = no_mid - no_bid
            size_ok = quantity >= Q_REWARD_MIN
            two_sided_reward_eligible = bool(
                cfg.get("enabled")
                and reward is not None
                and size_ok
                and yes_distance <= max_distance
                and no_distance <= max_distance
            )
            projected_rebates = yes_rounded_rebate + no_rounded_rebate
            rows.append({
                "ticker": event.ticker,
                "symbol": event.symbol,
                "asset": event.asset,
                "horizon": event.horizon,
                "seconds_to_expiry": round(event.seconds_to_expiry, 3),
                "quantity": str(quantity),
                "yes_bid": str(yes_bid),
                "yes_bid_qty": str(yes_bid_qty),
                "yes_ask": str(yes_ask),
                "yes_ask_qty": str(yes_ask_qty),
                "no_bid": str(no_bid),
                "spread": str(yes_ask - yes_bid),
                "raw_complete_set_discount": str((yes_ask - yes_bid) * quantity),
                "yes_maker_fee": str(yes_fee),
                "no_maker_fee": str(no_fee),
                "trade_only_locked_edge": str(pair.locked_edge),
                "trade_only_locked_edge_per_pair": str(pair.locked_edge_per_pair),
                "trade_only_is_arb": pair.is_arb,
                "trade_only_classification": pair.classification,
                "current_crypto_rebate_rate": str(rebate_rate),
                "projected_yes_maker_rebate_if_filled": str(yes_rounded_rebate),
                "projected_no_maker_rebate_if_filled": str(no_rounded_rebate),
                "projected_pair_rebate_if_both_filled": str(projected_rebates),
                "projected_pair_edge_if_rebates_later_paid": str(pair.locked_edge + projected_rebates),
                "projected_reward_warning": "NOT_REALIZED_AND_NOT_USED_TO_CLASSIFY_ARB",
                "liquidity_reward_pool_id": reward.get("pool_id") if reward else None,
                "liquidity_reward_daily_pool_usd": reward.get("daily_pool_usd") if reward else None,
                "liquidity_reward_pool_event_count": reward.get("pool_event_count") if reward else None,
                "reported_qualifying_maker_count": reward.get("qualifying_maker_count") if reward else None,
                "yes_distance_from_mid": str(yes_distance),
                "no_distance_from_mid": str(no_distance),
                "two_sided_reward_eligible_at_snapshot": two_sided_reward_eligible,
                "reward_eligibility_warning": "SNAPSHOT_ELIGIBLE_ONLY; DAILY_UPTIME_AND_COMPETITION_DETERMINE_PAYOUT",
            })

    good = [r for r in rows if "error" not in r]
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "classification": "READ_ONLY_SHADOW_SNAPSHOT_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "quantity": str(quantity),
        "active_crypto_rebate_rate": str(rebate_rate),
        "liquidity_rewards_enabled": bool(cfg.get("enabled")),
        "reward_min_quantity": str(Q_REWARD_MIN),
        "reward_max_distance_from_mid": str(max_distance),
        "markets": len(good),
        "trade_only_arb_markets": sum(1 for r in good if r["trade_only_is_arb"]),
        "two_sided_reward_eligible_markets_at_snapshot": sum(1 for r in good if r["two_sided_reward_eligible_at_snapshot"]),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quantity", default="10")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(scan(D(args.quantity)))
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True, default=str))


if __name__ == "__main__":
    main()

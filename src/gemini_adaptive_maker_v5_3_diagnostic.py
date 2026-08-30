"""Credential-free live-public diagnostic for Gemini adaptive maker V5.3.

No account calls and no order entry. It waits for an eligible BTC5 window, keeps the
same depth-5 subscription hot, selects the exact V5.3 adaptive plan, then simulates
our YES and NO maker orders inside the public depth ladder. The final standing rescue
check removes the simulated owned depth and proves the engine can see the next real
external level rather than counting its own orders.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal as D
import json
import time

from src import gemini_adaptive_maker_policy_v5_3 as policy
from src import gemini_q10_production_executor_v5 as v5
from src import gemini_q10_rescue_policy as rescue
from src.gemini_prediction_depth_cache_v5_3 import DepthSnapshot, GeminiDepth5Cache

ZERO = D("0")
ONE = D("1")
WAIT_SECONDS = 360.0
POLL_SECONDS = 2.0


def _add_level(levels, *, price: D, quantity: D, reverse: bool):
    aggregated = {D(str(p)): D(str(q)) for p, q in levels}
    p = D(str(price))
    aggregated[p] = aggregated.get(p, ZERO) + D(str(quantity))
    return tuple(sorted(aggregated.items(), key=lambda x: x[0], reverse=reverse)[:5])


def simulate_owned_orders(snap: DepthSnapshot, plan: policy.AdaptiveMakerPlan) -> DepthSnapshot:
    bids = _add_level(
        snap.bids,
        price=plan.yes_quote,
        quantity=plan.quantity,
        reverse=True,
    )
    asks = _add_level(
        snap.asks,
        price=ONE - plan.no_quote,
        quantity=plan.quantity,
        reverse=False,
    )
    return replace(snap, bids=bids, asks=asks, sequence=snap.sequence + 1)


def yes_only_route_after_simulation(
    original: DepthSnapshot,
    plan: policy.AdaptiveMakerPlan,
    *,
    increment: D,
):
    with_yes = replace(
        original,
        bids=_add_level(
            original.bids,
            price=plan.yes_quote,
            quantity=plan.quantity,
            reverse=True,
        ),
        sequence=original.sequence + 1,
    )
    external = with_yes.externalized(
        own_yes_price=plan.yes_quote,
        own_yes_quantity=plan.quantity,
    )
    if external is None:
        return None
    return rescue.best_route_after_fill(
        filled_outcome="YES",
        filled_price=plan.yes_quote,
        quantity=plan.quantity,
        yes_bid=external.bid,
        yes_bid_size=external.bid_size,
        yes_ask=external.ask,
        yes_ask_size=external.ask_size,
        max_loss=v5.max_rescue_loss(),
        increment=increment,
    )


async def run() -> dict:
    deadline = time.monotonic() + WAIT_SECONDS
    cache = None
    cache_stop = None
    cache_task = None
    cache_symbol = None
    polls = 0
    reasons: dict[str, int] = {}
    try:
        while time.monotonic() < deadline:
            polls += 1
            try:
                event = await v5.v1.base.discover_btc5m()
            except Exception as exc:
                key = f"EVENT_DISCOVERY_ERROR:{type(exc).__name__}"
                reasons[key] = reasons.get(key, 0) + 1
                await asyncio.sleep(POLL_SECONDS)
                continue
            if event is None:
                reasons["NO_ACTIVE_BTC5"] = reasons.get("NO_ACTIVE_BTC5", 0) + 1
                await asyncio.sleep(POLL_SECONDS)
                continue
            if not (
                D(str(event.quantity_minimum)) == D("1")
                and D(str(event.quantity_increment)) == D("1")
                and D(str(event.price_increment)) > ZERO
            ):
                reasons["UNSUPPORTED_CONTRACT_METADATA"] = reasons.get("UNSUPPORTED_CONTRACT_METADATA", 0) + 1
                await asyncio.sleep(POLL_SECONDS)
                continue
            if not (v5.v1.ENTRY_MIN_SECONDS <= event.seconds_to_expiry <= v5.v1.ENTRY_MAX_SECONDS):
                reasons["EXPIRY_WINDOW"] = reasons.get("EXPIRY_WINDOW", 0) + 1
                await asyncio.sleep(POLL_SECONDS)
                continue

            if cache_symbol != event.symbol:
                if cache_stop is not None:
                    cache_stop.set()
                if cache_task is not None:
                    cache_task.cancel()
                    await asyncio.gather(cache_task, return_exceptions=True)
                cache = GeminiDepth5Cache(event.symbol, stale_ms=750)
                cache_symbol = event.symbol
                cache_stop = asyncio.Event()
                cache_task = asyncio.create_task(cache.run(cache_stop))

            assert cache is not None
            try:
                snap = await cache.wait_fresh(timeout=4.0, max_age_ms=v5.BOOK_MAX_AGE_MS)
            except Exception as exc:
                key = f"DEPTH_NOT_FRESH:{type(exc).__name__}"
                reasons[key] = reasons.get(key, 0) + 1
                await asyncio.sleep(POLL_SECONDS)
                continue

            plan = policy.select_plan(
                snap=snap,
                increment=event.price_increment,
                max_loss=v5.max_rescue_loss(),
            )
            if plan is None:
                reasons["NO_SAFE_TRADE_EDGE_PLAN"] = reasons.get("NO_SAFE_TRADE_EDGE_PLAN", 0) + 1
                await asyncio.sleep(POLL_SECONDS)
                continue

            simulated = simulate_owned_orders(snap, plan)
            standing = policy.standing_rescue_for_plan(
                plan=plan,
                snap=simulated,
                increment=event.price_increment,
                yes_resting=True,
                no_resting=True,
                max_loss=v5.max_rescue_loss(),
            )
            yes_route = yes_only_route_after_simulation(
                snap,
                plan,
                increment=event.price_increment,
            )
            result = {
                "classification": "READ_ONLY_PUBLIC_NO_CREDENTIALS_NO_ORDER_ENTRY",
                "ticker": event.ticker,
                "symbol": event.symbol,
                "seconds_to_expiry": round(event.seconds_to_expiry, 3),
                "polls": polls,
                "reason_counts": reasons,
                "book": {
                    "bids": [[str(p), str(q)] for p, q in snap.bids],
                    "asks": [[str(p), str(q)] for p, q in snap.asks],
                    "age_ms": round(snap.age_ms, 3),
                    "sequence": snap.sequence,
                },
                "plan": plan.as_dict(),
                "yes_post_only_route": None if yes_route is None else yes_route.as_dict(),
                "yes_post_only_safe": bool(yes_route is not None and yes_route.safe),
                "both_posted_standing": None if standing is None else standing.as_dict(),
                "both_posted_safe": bool(standing is not None and standing.safe),
                "own_depth_removed": True,
                "liquidity_rewards_modeled": False,
                "maker_rebates_modeled": False,
                "order_entry": False,
                "realized_pnl": 0,
            }
            if not result["yes_post_only_safe"] or not result["both_posted_safe"]:
                result["classification"] = "READ_ONLY_PLAN_FAILED_POST_SIMULATION_SAFETY"
            return result
        return {
            "classification": "READ_ONLY_NO_SAFE_PLAN_WITHIN_WINDOW",
            "polls": polls,
            "reason_counts": reasons,
            "liquidity_rewards_modeled": False,
            "maker_rebates_modeled": False,
            "order_entry": False,
            "realized_pnl": 0,
        }
    finally:
        if cache_stop is not None:
            cache_stop.set()
        if cache_task is not None:
            cache_task.cancel()
            await asyncio.gather(cache_task, return_exceptions=True)


if __name__ == "__main__":
    result = asyncio.run(run())
    print("GEMINI_ADAPTIVE_MAKER_V5_3_DIAG=" + json.dumps(result, separators=(",", ":"), default=str))
    print("GEMINI_ADAPTIVE_MAKER_V5_3_DIAG_COMPLETE")

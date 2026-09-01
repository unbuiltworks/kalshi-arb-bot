"""Read-only shadow for the exact Gemini q10 production decision path.

Uses the same candidate selector, spot guard, entry model and dynamic completion policy
as the production executor. It never creates a trading session and never calls an
order mutation method. Output is explicitly shadow/not realized.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal as D
import json
import os
import time

from src.btc_spot_toxicity_guard import BtcSpotGuard
from src import gemini_q10_production_executor as prod
from src import gemini_liquidity_reward_dynamic_completion_q10 as completion


async def run() -> None:
    # A shadow process must not coexist with a live arm in the same environment.
    if prod.live_armed():
        raise RuntimeError("shadow refuses to run while production dual-key live arm is present")

    available, flat = await prod._preflight_flat()
    guard = BtcSpotGuard()
    stop = asyncio.Event()
    task = asyncio.create_task(guard.run_coinbase(stop))
    try:
        if not await prod._warm_spot(guard):
            return
        wait_s = float(os.getenv("GEMINI_Q10_SHADOW_CANDIDATE_WAIT_SECONDS", "90"))
        original_wait = prod.probe.QUALIFIER_WAIT_SECONDS
        prod.probe.QUALIFIER_WAIT_SECONDS = max(5.0, min(600.0, wait_s))
        try:
            t0 = time.perf_counter_ns()
            event, reward, initial = await prod.probe._wait_candidate()
            candidate_ms = (time.perf_counter_ns() - t0) / 1_000_000
        finally:
            prod.probe.QUALIFIER_WAIT_SECONDS = original_wait

        spot = guard.decision()
        t1 = time.perf_counter_ns()
        bid, bid_size, ask, ask_size = await prod.base.fresh_top(event.symbol)
        top_ms = (time.perf_counter_ns() - t1) / 1_000_000
        model = prod.probe._model(bid, ask)

        # Hypothetical post-fill plans exercise the same risk math without mutation.
        plans = {}
        for filled in ("YES", "NO"):
            filled_price = model["yes_quote"] if filled == "YES" else model["no_quote"]
            missing_price = model["no_quote"] if filled == "YES" else model["yes_quote"]
            try:
                plan = completion.completion_plan(
                    filled_outcome=filled,
                    filled_price=filled_price,
                    current_complement_price=missing_price,
                    yes_bid=bid,
                    yes_ask=ask,
                    seconds_to_expiry=event.seconds_to_expiry,
                    quantity=D("10"),
                    increment=event.price_increment,
                    min_price=D("0.01"),
                    max_price=D("0.99"),
                )
                plans[filled] = {k: str(v) for k, v in plan.items()}
            except Exception as exc:
                plans[filled] = {"error_type": type(exc).__name__}

        rr = reward.get("event") or {}
        makers = rr.get("qualifying_maker_count") if "qualifying_maker_count" in rr else rr.get("qualifyingMakerCount")
        output = {
            "classification": "READ_ONLY_EXACT_PRODUCTION_SHADOW_NOT_REALIZED",
            "order_entry": False,
            "credentials_used_for_account_read_only": True,
            "available_usd": str(available),
            "flat": flat,
            "ticker": event.ticker,
            "symbol": event.symbol,
            "seconds_to_expiry": event.seconds_to_expiry,
            "spot": asdict(spot),
            "candidate_wait_ms": round(candidate_ms, 3),
            "fresh_top_roundtrip_ms": round(top_ms, 3),
            "yes_bid": str(bid),
            "yes_bid_size": str(bid_size),
            "yes_ask": str(ask),
            "yes_ask_size": str(ask_size),
            "entry_model": {k: str(v) for k, v in model.items()},
            "entry_model_passes": prod._entry_model_ok(model),
            "reported_qualifying_maker_count": makers,
            "hypothetical_dynamic_completion": plans,
            "realized_pnl": None,
            "rewards_realized": None,
        }
        print("GEMINI_Q10_PRODUCTION_SHADOW " + json.dumps(output, separators=(",", ":"), default=str), flush=True)
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(run())

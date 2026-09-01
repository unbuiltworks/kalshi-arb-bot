"""Read-only shadow for the exact Gemini q10 V5 admission/risk path.

Uses authenticated account reads plus public market/spot data, but never opens a
trading session and never calls any mutation endpoint. A missing candidate is a
normal no-op, not a failed job.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
import time

from src import gemini_q10_production_executor_v5 as v5
from src.btc_spot_toxicity_guard import BtcSpotGuard
from src.gemini_prediction_book_cache import GeminiTopCache


async def run() -> None:
    if v5.live_armed():
        raise RuntimeError("V5 shadow refuses to run while production arm is present")

    available, flat = await v5.v1._preflight_flat()
    guard = BtcSpotGuard()
    spot_stop = asyncio.Event()
    spot_task = asyncio.create_task(guard.run_coinbase(spot_stop))
    try:
        if not await v5.v1._warm_spot(guard):
            print("GEMINI_Q10_V5_SHADOW=" + json.dumps({
                "classification":"READ_ONLY_V5_SHADOW_NOT_REALIZED",
                "order_entry":False,
                "status":"SPOT_NOT_READY",
            }, separators=(",", ":")))
            return

        wait_s = float(os.getenv("GEMINI_Q10_V5_SHADOW_CANDIDATE_WAIT_SECONDS", "90"))
        original_wait = v5.v1.probe.QUALIFIER_WAIT_SECONDS
        v5.v1.probe.QUALIFIER_WAIT_SECONDS = max(5.0, min(600.0, wait_s))
        try:
            t0 = time.perf_counter_ns()
            found = await v5._wait_candidate_or_none()
            candidate_ms = (time.perf_counter_ns() - t0) / 1_000_000
        finally:
            v5.v1.probe.QUALIFIER_WAIT_SECONDS = original_wait

        if found is None:
            print("GEMINI_Q10_V5_SHADOW=" + json.dumps({
                "classification":"READ_ONLY_V5_SHADOW_NOT_REALIZED",
                "order_entry":False,
                "status":"NO_CANDIDATE",
                "candidate_wait_ms":round(candidate_ms,3),
                "available_usd":str(available),
                "flat":flat,
                "max_rescue_loss":str(v5.max_rescue_loss()),
            }, separators=(",", ":"), default=str))
            return

        event, reward, _ = found
        cache = GeminiTopCache(event.symbol, stale_ms=750)
        book_stop = asyncio.Event()
        book_task = asyncio.create_task(cache.run(book_stop))
        try:
            snap = await cache.wait_fresh(timeout=3.0, max_age_ms=v5.BOOK_MAX_AGE_MS)
            model = v5.v1.probe._model(snap.bid, snap.ask)
            standing = v5._standing(model, snap, increment=event.price_increment)
            spot = guard.decision()
            rr = reward.get("event") or {}
            makers = rr.get("qualifying_maker_count") if "qualifying_maker_count" in rr else rr.get("qualifyingMakerCount")
            output = {
                "classification":"READ_ONLY_V5_SHADOW_NOT_REALIZED",
                "order_entry":False,
                "status":"CANDIDATE_EVALUATED",
                "available_usd":str(available),
                "flat":flat,
                "ticker":event.ticker,
                "symbol":event.symbol,
                "seconds_to_expiry":event.seconds_to_expiry,
                "candidate_wait_ms":round(candidate_ms,3),
                "book_age_ms":round(snap.age_ms,3),
                "yes_bid":str(snap.bid),
                "yes_bid_size":str(snap.bid_size),
                "yes_ask":str(snap.ask),
                "yes_ask_size":str(snap.ask_size),
                "entry_model":{k:str(v) for k,v in model.items()},
                "entry_model_passes":v5.v1._entry_model_ok(model),
                "standing_rescue":standing.as_dict(),
                "v5_would_enter":bool(v5.v1._entry_model_ok(model) and standing.safe and spot.ready and not spot.dangerous and int(makers)==0),
                "spot":asdict(spot),
                "reported_qualifying_maker_count":makers,
                "max_rescue_loss":str(v5.max_rescue_loss()),
                "max_campaign_loss":str(v5.max_campaign_loss()),
                "max_daily_loss":str(v5.max_daily_loss()),
                "realized_pnl":None,
                "rewards_realized":None,
            }
            print("GEMINI_Q10_V5_SHADOW=" + json.dumps(output,separators=(",", ":"),default=str), flush=True)
        finally:
            book_stop.set()
            book_task.cancel()
            await asyncio.gather(book_task, return_exceptions=True)
    finally:
        spot_stop.set()
        spot_task.cancel()
        await asyncio.gather(spot_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())

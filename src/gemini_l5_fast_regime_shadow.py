"""High-frequency, read-only Gemini l5 regime capture.

This probe exists only to resolve ordering around adverse strict trade-throughs.
It discovers the nearest BTC/ETH 5m+15m prediction events periodically, then
samples their public REST books much faster than the normal 2s research shadow.
No credentials or order-entry code are used.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

import httpx

from src.gemini_l5_threshold_model import plan_threshold_quotes
from src.gemini_l5_threshold_shadow import discover, book


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def capture(*, seconds: float, poll_s: float, discover_s: float, output: Path) -> dict[str, Any]:
    if poll_s < 0.1:
        raise ValueError("poll_s must be >= 0.1")
    if discover_s < poll_s:
        raise ValueError("discover_s must be >= poll_s")
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, seconds)
    next_discover = 0.0
    events = {}
    stats = {"samples": 0, "errors": 0, "discoveries": 0, "poll_s": poll_s, "discover_s": discover_s}

    async with httpx.AsyncClient(timeout=4.0, follow_redirects=True, headers={"User-Agent":"arb-gemini-l5-fast-regime-shadow/1.0"}) as http:
        with output.open("w", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                loop_started = time.monotonic()
                if loop_started >= next_discover or not events:
                    try:
                        events = await discover(http)
                        stats["discoveries"] += 1
                    except Exception as exc:
                        stats["errors"] += 1
                        print(json.dumps({"type":"GEMINI_L5_FAST_DISCOVERY_ERROR","observed_at":_iso(),"error_type":type(exc).__name__,"error":str(exc)[:240],"read_only":True,"order_entry":False}), flush=True)
                    next_discover = time.monotonic() + discover_s

                async def sample(event):
                    try:
                        bid, bid_size, ask, ask_size = await book(http, event.symbol)
                        plan = plan_threshold_quotes(bid, ask, price_increment=event.price_increment)
                        return {
                            "type":"GEMINI_L5_THRESHOLD_SHADOW",
                            "capture_mode":"FAST_REGIME_READ_ONLY",
                            "observed_at":_iso(),
                            "asset":event.asset,
                            "horizon":event.horizon,
                            "ticker":event.ticker,
                            "symbol":event.symbol,
                            "seconds_to_expiry":round(event.seconds_to_expiry,3),
                            "price_increment":str(event.price_increment),
                            "yes_bid":str(bid),"yes_bid_size":str(bid_size),
                            "yes_ask":str(ask),"yes_ask_size":str(ask_size),
                            "yes_mid":str(plan.yes_mid),"no_mid":str(plan.no_mid),
                            "favored_side":plan.favored_side,
                            "favored_probability":str(plan.favored_probability),
                            "shadow_target_units":plan.shadow_target_units,
                            "shadow_yes_quote":str(plan.yes_quote),
                            "shadow_no_quote":str(plan.no_quote),
                            "quote_skew_ticks":plan.quote_skew_ticks,
                            "pair_cost_if_both_fill":str(plan.pair_cost),
                            "modeled_pair_edge_after_maker_fees":str(plan.modeled_edge),
                            "yes_maker_fee_q1":str(plan.yes_fee),
                            "no_maker_fee_q1":str(plan.no_fee),
                            "yes_rebate_eligible":plan.yes_rebate_eligible,
                            "no_rebate_eligible":plan.no_rebate_eligible,
                            "q1_yes_rebate_raw":str(plan.q1_yes_rebate_raw),
                            "q1_no_rebate_raw":str(plan.q1_no_rebate_raw),
                            "q1_rebate_cent_rounded_total":str(plan.q1_yes_rebate_rounded + plan.q1_no_rebate_rounded),
                            "reward_pool_active":False,
                            "fill_status":"NO_FILL_ASSUMED",
                            "trading_pnl_status":"SHADOW_NOT_REALIZED",
                            "rebate_status":"MODELED_NOT_REALIZED",
                            "read_only":True,"order_entry":False,
                        }
                    except Exception as exc:
                        return {"type":"GEMINI_L5_FAST_SAMPLE_ERROR","observed_at":_iso(),"ticker":getattr(event,'ticker',None),"error_type":type(exc).__name__,"error":str(exc)[:240],"read_only":True,"order_entry":False}

                results = await asyncio.gather(*(sample(event) for event in events.values()))
                for row in results:
                    if row.get("type") == "GEMINI_L5_THRESHOLD_SHADOW":
                        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                        stats["samples"] += 1
                    else:
                        stats["errors"] += 1
                fh.flush()
                elapsed = time.monotonic() - loop_started
                await asyncio.sleep(max(0.01, poll_s - elapsed))

    result = {"type":"GEMINI_L5_FAST_REGIME_SHADOW_SUMMARY",**stats,"read_only":True,"order_entry":False,"realized_pnl":None}
    print(json.dumps(result,separators=(",", ":")), flush=True)
    return result


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--seconds",type=float,default=600.0)
    parser.add_argument("--poll-s",type=float,default=0.25)
    parser.add_argument("--discover-s",type=float,default=15.0)
    parser.add_argument("--output",type=Path,default=Path("artifacts/gemini-l5-fast-regime.jsonl"))
    args=parser.parse_args()
    asyncio.run(capture(seconds=args.seconds,poll_s=args.poll_s,discover_s=args.discover_s,output=args.output))


if __name__ == "__main__":
    main()

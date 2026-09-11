"""Read-only Gemini BTC5 reward/rescue clip-size diagnostic.

Purpose: answer a narrower execution question than the q10 qualifier:
for a live zero-competition BTC5 reward window, what quantity q=1..10 can
simultaneously satisfy:
- reward-distance geometry using the smallest inward quote move;
- maker-only price geometry;
- the existing 20c..80c price band;
- the same 5c/contract after-fee pair-edge floor as q10's $0.50 floor;
- the same absolute $0.30 first-fill rescue-loss bound;
- current top-level depth.

No credentials, state mounts, or order-entry modules are used. A persistent public
Gemini depth cache remains connected across polls so quiet-book periods are measured
rather than repeatedly reconnecting.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D, ROUND_CEILING
import json
import time
from typing import Any, Mapping

from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_q10_rescue_policy as rescue
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_prediction_book_cache import GeminiTopCache

ONE = D("1")
ZERO = D("0")
CENT = D("0.01")
MAX_LOSS = D("0.30")
EDGE_PER_CONTRACT = D("0.05")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
WAIT_SECONDS = 360.0
POLL_SECONDS = 5.0


def ceil_to_increment(value: D, increment: D) -> D:
    return (value / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def reward_boundary_quotes(*, bid: D, ask: D, max_distance: D, increment: D) -> dict[str, Any]:
    mid = (bid + ask) / D("2")
    no_mid = ONE - mid
    yes_base = bid
    no_base = ONE - ask
    yes_quote = max(yes_base, ceil_to_increment(mid - max_distance, increment))
    no_quote = max(no_base, ceil_to_increment(no_mid - max_distance, increment))
    # Maker-only ceilings: one tick inside the opposite executable side.
    yes_passive_ceiling = ask - increment
    no_passive_ceiling = (ONE - bid) - increment
    maker_ok = bool(yes_quote <= yes_passive_ceiling and no_quote <= no_passive_ceiling)
    return {
        "mid": mid,
        "yes_base": yes_base,
        "no_base": no_base,
        "yes_quote": yes_quote,
        "no_quote": no_quote,
        "yes_distance": mid - yes_quote,
        "no_distance": no_mid - no_quote,
        "yes_passive_ceiling": yes_passive_ceiling,
        "no_passive_ceiling": no_passive_ceiling,
        "maker_ok": maker_ok,
    }


def pair_profit(*, yes_quote: D, no_quote: D, quantity: D) -> D:
    return (
        quantity * (ONE - yes_quote - no_quote)
        - gemini_predictions_maker_fee(yes_quote, quantity)
        - gemini_predictions_maker_fee(no_quote, quantity)
    )


def evaluate_quantity(*, q: int, quotes: Mapping[str, Any], snap: Any, increment: D) -> dict[str, Any]:
    qty = D(q)
    yes = D(str(quotes["yes_quote"]))
    no = D(str(quotes["no_quote"]))
    profit = pair_profit(yes_quote=yes, no_quote=no, quantity=qty)
    edge_floor = EDGE_PER_CONTRACT * qty
    standing = rescue.standing_rescue_check(
        yes_quote=yes,
        no_quote=no,
        quantity=qty,
        yes_bid=snap.bid,
        yes_bid_size=snap.bid_size,
        yes_ask=snap.ask,
        yes_ask_size=snap.ask_size,
        max_loss=MAX_LOSS,
        increment=increment,
    )
    price_ok = MIN_PRICE <= yes <= MAX_PRICE and MIN_PRICE <= no <= MAX_PRICE
    safe = bool(quotes["maker_ok"] and price_ok and profit >= edge_floor and standing.safe)
    return {
        "q": q,
        "safe": safe,
        "pair_profit": str(profit),
        "edge_floor": str(edge_floor),
        "price_ok": price_ok,
        "maker_ok": bool(quotes["maker_ok"]),
        "rescue_safe": standing.safe,
        "worst_rescue_loss": str(standing.worst_current_loss),
        "rescue": standing.as_dict(),
    }


async def main() -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    cache: GeminiTopCache | None = None
    cache_symbol: str | None = None
    cache_stop: asyncio.Event | None = None
    cache_task: asyncio.Task | None = None
    samples: list[dict[str, Any]] = []
    polls = 0

    async def close_cache() -> None:
        nonlocal cache, cache_symbol, cache_stop, cache_task
        if cache_stop is not None:
            cache_stop.set()
        if cache_task is not None:
            cache_task.cancel()
            await asyncio.gather(cache_task, return_exceptions=True)
        cache = None
        cache_symbol = None
        cache_stop = None
        cache_task = None

    try:
        while time.monotonic() < deadline:
            polls += 1
            event = await probe.base.discover_btc5m()
            if event is None or not (probe.MIN_SECONDS_TO_EXPIRY <= event.seconds_to_expiry <= probe.MAX_SECONDS_TO_EXPIRY):
                await asyncio.sleep(POLL_SECONDS)
                continue
            reward = await probe._reward_snapshot(event.ticker)
            if reward is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            row = reward["event"]
            makers_raw = row.get("qualifying_maker_count") if "qualifying_maker_count" in row else row.get("qualifyingMakerCount")
            try:
                makers = int(makers_raw)
            except Exception:
                await asyncio.sleep(POLL_SECONDS)
                continue
            if makers != 0:
                await asyncio.sleep(POLL_SECONDS)
                continue

            if cache_symbol != event.symbol:
                await close_cache()
                cache = GeminiTopCache(event.symbol, stale_ms=1000)
                cache_symbol = event.symbol
                cache_stop = asyncio.Event()
                cache_task = asyncio.create_task(cache.run(cache_stop))

            assert cache is not None
            try:
                snap = await cache.wait_fresh(timeout=4.0, max_age_ms=1000)
            except Exception as exc:
                samples.append({
                    "ticker": event.ticker,
                    "seconds_to_expiry": round(event.seconds_to_expiry, 3),
                    "classification": "DEPTH_WAIT",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:240],
                })
                await asyncio.sleep(POLL_SECONDS)
                continue

            cfg = reward["config"]
            max_distance = D(str(cfg.get("max_spread_cents") or 10)) / D("100")
            quotes = reward_boundary_quotes(
                bid=D(str(snap.bid)), ask=D(str(snap.ask)),
                max_distance=max_distance, increment=event.price_increment,
            )
            rows = [evaluate_quantity(q=q, quotes=quotes, snap=snap, increment=event.price_increment) for q in range(1, 11)]
            safe = [r for r in rows if r["safe"]]
            samples.append({
                "ticker": event.ticker,
                "symbol": event.symbol,
                "seconds_to_expiry": round(event.seconds_to_expiry, 3),
                "classification": "CLIP_EVALUATED",
                "bid": str(snap.bid),
                "bid_size": str(snap.bid_size),
                "ask": str(snap.ask),
                "ask_size": str(snap.ask_size),
                "max_distance": str(max_distance),
                "quotes": {k: (str(v) if isinstance(v, D) else v) for k, v in quotes.items()},
                "max_safe_q": max((r["q"] for r in safe), default=0),
                "q10": rows[-1],
                "safe_rows": safe,
            })
            if safe:
                break
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await close_cache()

    print("V5_2_REWARD_RESCUE_CLIP_DIAG=" + json.dumps({
        "classification": "READ_ONLY_NO_ORDER_ENTRY",
        "polls": polls,
        "samples": samples[-12:],
        "found_safe_clip": any(s.get("max_safe_q", 0) > 0 for s in samples),
        "order_entry": False,
        "realized_pnl": 0,
    }, separators=(",", ":"), default=str))
    print("V5_2_REWARD_RESCUE_CLIP_DIAG_COMPLETE")


if __name__ == "__main__":
    asyncio.run(main())

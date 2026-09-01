"""Read-only low-latency BTC5 q10 max-width + Coinbase spot-guard shadow.

Tests the production hypothesis that a q10 pair placed ~9c from Gemini midpoint can
collect liquidity-reward snapshots with low fill risk, while the faster Coinbase BTC
feed pulls the virtual pair before a rapid prediction-book move reaches either quote.

No private endpoints and no order mutation. A "cross" is only a conservative proxy
for a resting q10 becoming marketable/fillable.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal as D, ROUND_CEILING
import json
import os
import time
from typing import Any, Mapping

from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_q10_rescue_policy as rescue
from src.btc_spot_toxicity_guard import BtcSpotGuard
from src.gemini_prediction_book_cache import GeminiTopCache
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

Q = D("10")
ONE = D("1")
RUN_SECONDS = float(os.getenv("GEMINI_Q10_SPOTGUARD_SHADOW_SECONDS", "1200"))
LOOP_SECONDS = float(os.getenv("GEMINI_Q10_SPOTGUARD_SHADOW_LOOP_SECONDS", "0.05"))
TARGET_DISTANCE = D(os.getenv("GEMINI_Q10_SPOTGUARD_TARGET_DISTANCE", "0.09"))
MAX_RESCUE_LOSS = D(os.getenv("GEMINI_Q10_SPOTGUARD_MAX_RESCUE_LOSS", "0.50"))
CALM_REPOST_SECONDS = float(os.getenv("GEMINI_Q10_SPOTGUARD_CALM_REPOST_SECONDS", "0.50"))


def emit(stage: str, **fields: Any) -> None:
    print("GEMINI_Q10_SPOTGUARD_SHADOW=" + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str), flush=True)


def _maker_count(row: Mapping[str, Any]) -> int | None:
    raw = row.get("qualifying_maker_count") if "qualifying_maker_count" in row else row.get("qualifyingMakerCount")
    try:
        return int(raw)
    except Exception:
        return None


def _ceil_tick(value: D, tick: D) -> D:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _target(mid: D, tick: D) -> tuple[D, D]:
    return _ceil_tick(mid - TARGET_DISTANCE, tick), _ceil_tick((ONE - mid) - TARGET_DISTANCE, tick)


def _evaluate(*, yes_quote: D, no_quote: D, bid: D, bid_size: D, ask: D, ask_size: D, tick: D, max_distance: D) -> dict[str, Any]:
    mid = (bid + ask) / D("2")
    yes_distance = mid - yes_quote
    no_distance = (ONE - mid) - no_quote
    reward_ok = bool(
        D("0") <= yes_distance <= max_distance
        and D("0") <= no_distance <= max_distance
        and probe.MIN_PRICE <= yes_quote <= probe.MAX_PRICE
        and probe.MIN_PRICE <= no_quote <= probe.MAX_PRICE
    )
    yes_cross = ask <= yes_quote
    no_cross = (ONE - bid) <= no_quote
    maker_ok = not yes_cross and not no_cross
    locked_edge = Q * (ONE - yes_quote - no_quote) - gemini_predictions_maker_fee(yes_quote, Q) - gemini_predictions_maker_fee(no_quote, Q)
    standing = rescue.standing_rescue_check(
        yes_quote=yes_quote, no_quote=no_quote, quantity=Q,
        yes_bid=bid, yes_bid_size=bid_size, yes_ask=ask, yes_ask_size=ask_size,
        max_loss=MAX_RESCUE_LOSS, increment=tick,
    )
    return {
        "yes_quote": str(yes_quote), "no_quote": str(no_quote),
        "yes_distance": str(yes_distance), "no_distance": str(no_distance),
        "reward_ok": reward_ok, "maker_ok": maker_ok,
        "yes_cross": yes_cross, "no_cross": no_cross,
        "locked_edge": str(locked_edge), "rescue_safe": bool(standing.safe),
        "qualified_safe": bool(reward_ok and maker_ok and standing.safe and locked_edge > D("0")),
        "rescue": standing.as_dict(),
    }


async def run() -> None:
    if RUN_SECONDS <= 0 or LOOP_SECONDS <= 0:
        raise RuntimeError("invalid shadow timing")
    guard = BtcSpotGuard(window_ms=2000, stale_ms=1500, pull_move_bps=D("5"))
    spot_stop = asyncio.Event()
    spot_task = asyncio.create_task(guard.run_coinbase(spot_stop))

    cache: GeminiTopCache | None = None
    cache_stop: asyncio.Event | None = None
    cache_task: asyncio.Task | None = None
    symbol: str | None = None
    ticker: str | None = None
    yes_quote: D | None = None
    no_quote: D | None = None
    calm_since: float | None = None
    posts = pulls = recenter_pulls = spot_pulls = crosses = guarded_crosses = unguarded_crosses = 0
    minute_total = minute_qualified = 0
    last_minute: int | None = None
    last_reward_refresh = 0.0
    reward: dict[str, Any] | None = None
    last_book_seq = -1
    deadline = time.monotonic() + RUN_SECONDS

    emit("START", run_seconds=RUN_SECONDS, loop_seconds=LOOP_SECONDS, target_distance=str(TARGET_DISTANCE), max_rescue_loss=str(MAX_RESCUE_LOSS), spot_pull_bps="5", read_only=True, order_entry=False)
    try:
        while time.monotonic() < deadline:
            try:
                event = await probe.base.discover_btc5m()
                if event is None:
                    await asyncio.sleep(LOOP_SECONDS); continue
                if symbol != event.symbol:
                    if cache_stop is not None:
                        cache_stop.set()
                    if cache_task is not None:
                        cache_task.cancel(); await asyncio.gather(cache_task, return_exceptions=True)
                    symbol, ticker = event.symbol, event.ticker
                    yes_quote = no_quote = None
                    cache = GeminiTopCache(symbol, stale_ms=750)
                    cache_stop = asyncio.Event()
                    cache_task = asyncio.create_task(cache.run(cache_stop))
                    last_book_seq = -1
                    reward = await probe._reward_snapshot(event.ticker)
                    last_reward_refresh = time.monotonic()
                    emit("NEW_MARKET", ticker=ticker, symbol=symbol, seconds_to_expiry=round(event.seconds_to_expiry,3), read_only=True, order_entry=False)

                assert cache is not None
                try:
                    snap = await cache.wait_fresh(timeout=0.20, max_age_ms=500)
                except Exception:
                    if yes_quote is not None:
                        pulls += 1; yes_quote = no_quote = None
                        emit("VIRTUAL_PULL", reason="BOOK_STALE", read_only=True, order_entry=False)
                    await asyncio.sleep(LOOP_SECONDS); continue

                now = time.monotonic()
                if now - last_reward_refresh >= 5.0:
                    reward = await probe._reward_snapshot(event.ticker)
                    last_reward_refresh = now
                if reward is None:
                    if yes_quote is not None:
                        pulls += 1; yes_quote = no_quote = None
                        emit("VIRTUAL_PULL", reason="REWARD_INACTIVE", read_only=True, order_entry=False)
                    await asyncio.sleep(LOOP_SECONDS); continue

                row = reward["event"]; cfg = reward["config"]
                makers = _maker_count(row)
                pool = str(row.get("daily_pool_usd") or row.get("dailyPoolUsd") or "")
                max_distance = D(str(cfg.get("max_spread_cents") or 10)) / D("100")
                spot = guard.decision()
                bid, bid_size, ask, ask_size = snap.bid, snap.bid_size, snap.ask, snap.ask_size
                tick = D(str(event.price_increment))

                if spot.ready and not spot.dangerous:
                    calm_since = calm_since or now
                else:
                    calm_since = None

                # Detect a conservative fill/cross proxy BEFORE deciding to pull.
                if yes_quote is not None and no_quote is not None and snap.sequence != last_book_seq:
                    current = _evaluate(yes_quote=yes_quote, no_quote=no_quote, bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, tick=tick, max_distance=max_distance)
                    if current["yes_cross"] or current["no_cross"]:
                        crosses += 1
                        if spot.dangerous or not spot.ready:
                            guarded_crosses += 1
                        else:
                            unguarded_crosses += 1
                        emit("VIRTUAL_CROSS", ticker=ticker, quote=current, spot=asdict(spot), guarded=bool(spot.dangerous or not spot.ready), book_age_ms=round(snap.age_ms,3), read_only=True, order_entry=False)
                        yes_quote = no_quote = None
                last_book_seq = snap.sequence

                if yes_quote is not None and no_quote is not None:
                    current = _evaluate(yes_quote=yes_quote, no_quote=no_quote, bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, tick=tick, max_distance=max_distance)
                    reason = None
                    if not spot.ready or spot.dangerous:
                        reason = "SPOT_GUARD"; spot_pulls += 1
                    elif not current["qualified_safe"]:
                        reason = "QUOTE_NO_LONGER_SAFE_OR_QUALIFIED"; recenter_pulls += 1
                    if reason:
                        pulls += 1
                        emit("VIRTUAL_PULL", reason=reason, quote=current, spot=asdict(spot), pool_usd=pool, qualifying_maker_count=makers, read_only=True, order_entry=False)
                        yes_quote = no_quote = None

                if yes_quote is None and calm_since is not None and now - calm_since >= CALM_REPOST_SECONDS:
                    mid = (bid + ask) / D("2")
                    y, n = _target(mid, tick)
                    candidate = _evaluate(yes_quote=y, no_quote=n, bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, tick=tick, max_distance=max_distance)
                    if candidate["qualified_safe"]:
                        yes_quote, no_quote = y, n
                        posts += 1
                        emit("VIRTUAL_POST", ticker=ticker, quote=candidate, spot=asdict(spot), pool_usd=pool, qualifying_maker_count=makers, posts=posts, read_only=True, order_entry=False)

                minute = int(time.time() // 60)
                if minute != last_minute:
                    last_minute = minute; minute_total += 1
                    qualified = False; quote = None
                    if yes_quote is not None and no_quote is not None:
                        quote = _evaluate(yes_quote=yes_quote, no_quote=no_quote, bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, tick=tick, max_distance=max_distance)
                        qualified = bool(quote["qualified_safe"] and spot.ready and not spot.dangerous)
                    if qualified: minute_qualified += 1
                    emit("MINUTE_SNAPSHOT_PROXY", ticker=ticker, qualified=qualified, quote=quote, spot=asdict(spot), minute_qualified=minute_qualified, minute_total=minute_total, posts=posts, pulls=pulls, crosses=crosses, guarded_crosses=guarded_crosses, unguarded_crosses=unguarded_crosses, pool_usd=pool, qualifying_maker_count=makers, read_only=True, order_entry=False)
            except Exception as exc:
                emit("LOOP_ERROR", error_type=type(exc).__name__, error_message=str(exc)[:240], read_only=True, order_entry=False)
            await asyncio.sleep(LOOP_SECONDS)
    finally:
        if cache_stop is not None: cache_stop.set()
        if cache_task is not None:
            cache_task.cancel(); await asyncio.gather(cache_task, return_exceptions=True)
        spot_stop.set(); spot_task.cancel(); await asyncio.gather(spot_task, return_exceptions=True)

    mdenom = max(minute_total, 1)
    emit("SUMMARY", minute_qualified=minute_qualified, minute_total=minute_total, minute_qualified_pct=round(100.0*minute_qualified/mdenom,3), posts=posts, pulls=pulls, spot_pulls=spot_pulls, recenter_pulls=recenter_pulls, crosses=crosses, guarded_crosses=guarded_crosses, unguarded_crosses=unguarded_crosses, read_only=True, order_entry=False, realized_pnl=None, rewards_realized=None)


if __name__ == "__main__":
    asyncio.run(run())

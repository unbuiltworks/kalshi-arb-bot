"""Read-only BTC5 q10 max-width liquidity-reward shadow.

Tests the low-fill-risk reward-farming design: quote minimum qualifying size (q10)
near the *widest* Gemini reward-eligible distance from midpoint, rather than joining
the top of book. Recenter only when midpoint drift would make the resting pair stop
qualifying or violate the standing rescue bound.

No private endpoints and no order mutation.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D, ROUND_CEILING
import json
import os
import time
from typing import Any, Mapping

from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_q10_rescue_policy as rescue
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

Q = D("10")
ONE = D("1")
RUN_SECONDS = float(os.getenv("GEMINI_Q10_MAXWIDTH_SHADOW_SECONDS", "1200"))
POLL_SECONDS = float(os.getenv("GEMINI_Q10_MAXWIDTH_SHADOW_POLL_SECONDS", "1.0"))
TARGET_DISTANCE = D(os.getenv("GEMINI_Q10_MAXWIDTH_TARGET_DISTANCE", "0.09"))
MAX_RESCUE_LOSS = D(os.getenv("GEMINI_Q10_MAXWIDTH_RESCUE_LOSS", "0.50"))


def emit(stage: str, **fields: Any) -> None:
    print("GEMINI_Q10_MAXWIDTH_SHADOW=" + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str), flush=True)


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
    # Post-only/maker geometry: BUY YES below YES ask; BUY NO below NO ask (=1-YES bid).
    maker_ok = bool(yes_quote < ask and no_quote < (ONE - bid))
    crossed_proxy = bool(ask <= yes_quote or (ONE - bid) <= no_quote)
    yes_fee = gemini_predictions_maker_fee(yes_quote, Q)
    no_fee = gemini_predictions_maker_fee(no_quote, Q)
    locked_edge = Q * (ONE - yes_quote - no_quote) - yes_fee - no_fee
    standing = rescue.standing_rescue_check(
        yes_quote=yes_quote,
        no_quote=no_quote,
        quantity=Q,
        yes_bid=bid,
        yes_bid_size=bid_size,
        yes_ask=ask,
        yes_ask_size=ask_size,
        max_loss=MAX_RESCUE_LOSS,
        increment=tick,
    )
    return {
        "yes_quote": str(yes_quote),
        "no_quote": str(no_quote),
        "yes_distance": str(yes_distance),
        "no_distance": str(no_distance),
        "reward_ok": reward_ok,
        "maker_ok": maker_ok,
        "crossed_proxy": crossed_proxy,
        "locked_edge": str(locked_edge),
        "rescue_safe": bool(standing.safe),
        "rescue": standing.as_dict(),
        "qualified_safe": bool(reward_ok and maker_ok and not crossed_proxy and standing.safe and locked_edge > D("0")),
    }


async def run() -> None:
    deadline = time.monotonic() + RUN_SECONDS
    symbol: str | None = None
    anchor_yes: D | None = None
    anchor_no: D | None = None
    recenters = 0
    samples = 0
    qualified_samples = 0
    zero_comp_samples = 0
    minute_total = 0
    minute_qualified = 0
    last_minute: int | None = None

    emit("START", run_seconds=RUN_SECONDS, poll_seconds=POLL_SECONDS, target_distance=str(TARGET_DISTANCE), max_rescue_loss=str(MAX_RESCUE_LOSS), read_only=True, order_entry=False)
    while time.monotonic() < deadline:
        try:
            event = await probe.base.discover_btc5m()
            if event is None:
                await asyncio.sleep(POLL_SECONDS); continue
            reward = await probe._reward_snapshot(event.ticker)
            if reward is None:
                await asyncio.sleep(POLL_SECONDS); continue
            row = reward["event"]
            cfg = reward["config"]
            max_distance = D(str(cfg.get("max_spread_cents") or 10)) / D("100")
            makers = _maker_count(row)
            pool = str(row.get("daily_pool_usd") or row.get("dailyPoolUsd") or "")
            bid, bid_size, ask, ask_size = await probe.base.fresh_top(event.symbol)
            bid = D(str(bid)); bid_size = D(str(bid_size)); ask = D(str(ask)); ask_size = D(str(ask_size)); tick = D(str(event.price_increment))
            mid = (bid + ask) / D("2")

            if symbol != event.symbol:
                symbol = event.symbol
                anchor_yes = anchor_no = None
                emit("NEW_MARKET", ticker=event.ticker, symbol=event.symbol, seconds_to_expiry=round(event.seconds_to_expiry,3), bid=str(bid), ask=str(ask), pool_usd=pool, qualifying_maker_count=makers)

            if anchor_yes is None or anchor_no is None:
                y, n = _target(mid, tick)
                candidate = _evaluate(yes_quote=y, no_quote=n, bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, tick=tick, max_distance=max_distance)
                if candidate["qualified_safe"]:
                    anchor_yes, anchor_no = y, n
                    emit("VIRTUAL_POST", ticker=event.ticker, **candidate, pool_usd=pool, qualifying_maker_count=makers)

            current = None
            if anchor_yes is not None and anchor_no is not None:
                current = _evaluate(yes_quote=anchor_yes, no_quote=anchor_no, bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, tick=tick, max_distance=max_distance)
                if not current["qualified_safe"]:
                    y, n = _target(mid, tick)
                    replacement = _evaluate(yes_quote=y, no_quote=n, bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, tick=tick, max_distance=max_distance)
                    if replacement["qualified_safe"]:
                        old_y, old_n = anchor_yes, anchor_no
                        anchor_yes, anchor_no = y, n
                        recenters += 1
                        current = replacement
                        emit("VIRTUAL_RECENTER", ticker=event.ticker, recenter_no=recenters, old_yes=str(old_y), old_no=str(old_n), **replacement, pool_usd=pool, qualifying_maker_count=makers)
                    else:
                        anchor_yes = anchor_no = None
                        emit("VIRTUAL_PULL", ticker=event.ticker, reason="NO_SAFE_QUALIFYING_REPLACEMENT", previous=current, replacement=replacement, pool_usd=pool, qualifying_maker_count=makers)
                        current = None

            samples += 1
            if makers == 0:
                zero_comp_samples += 1
            qualified = bool(current and current["qualified_safe"])
            if qualified:
                qualified_samples += 1

            minute = int(time.time() // 60)
            if minute != last_minute:
                last_minute = minute
                minute_total += 1
                if qualified:
                    minute_qualified += 1
                emit("MINUTE_SNAPSHOT_PROXY", ticker=event.ticker, qualified=qualified, quote=current, minute_qualified=minute_qualified, minute_total=minute_total, recenters=recenters, bid=str(bid), ask=str(ask), pool_usd=pool, qualifying_maker_count=makers, read_only=True, order_entry=False)
        except Exception as exc:
            emit("SAMPLE_ERROR", error_type=type(exc).__name__, error_message=str(exc)[:240], read_only=True, order_entry=False)
        await asyncio.sleep(POLL_SECONDS)

    denom = max(samples, 1); mdenom = max(minute_total, 1)
    emit("SUMMARY", samples=samples, qualified_samples=qualified_samples, qualified_pct=round(100.0*qualified_samples/denom,3), zero_competition_pct=round(100.0*zero_comp_samples/denom,3), minute_qualified=minute_qualified, minute_total=minute_total, minute_qualified_pct=round(100.0*minute_qualified/mdenom,3), recenters=recenters, read_only=True, order_entry=false if False else False, realized_pnl=None, rewards_realized=None)


if __name__ == "__main__":
    asyncio.run(run())

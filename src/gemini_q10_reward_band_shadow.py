"""Read-only Gemini BTC5 q10 reward-band geometry shadow.

Measures the actual tradeoff we care about for Gemini Liquidity Rewards:
- quote inside the published <=10c midpoint band at q=10;
- keep both sides maker-only;
- compute fee-adjusted complete-pair edge;
- compute the bounded single-fill rescue loss required by the live book;
- compare 5c/7c/9c/10c midpoint distances and $0.50/$0.75/$1.00 rescue budgets.

No private endpoints, no order placement, no cancellation, no account mutation.
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
RUN_SECONDS = float(os.getenv("GEMINI_Q10_REWARD_BAND_SHADOW_SECONDS", "1200"))
POLL_SECONDS = float(os.getenv("GEMINI_Q10_REWARD_BAND_SHADOW_POLL_SECONDS", "1.0"))
DISTANCES = (D("0.10"), D("0.09"), D("0.07"), D("0.05"))
BUDGETS = (D("0.50"), D("0.75"), D("1.00"))
LARGE_RESCUE_BUDGET = D("5.00")


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_Q10_REWARD_BAND_SHADOW="
        + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str),
        flush=True,
    )


def _maker_count(row: Mapping[str, Any]) -> int | None:
    raw = row.get("qualifying_maker_count") if "qualifying_maker_count" in row else row.get("qualifyingMakerCount")
    try:
        return int(raw)
    except Exception:
        return None


def _ceil_tick(value: D, tick: D) -> D:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _quote_geometry(*, bid: D, bid_size: D, ask: D, ask_size: D, tick: D, distance: D) -> dict[str, Any]:
    mid = (bid + ask) / D("2")
    yes_quote = _ceil_tick(mid - distance, tick)
    no_quote = _ceil_tick((ONE - mid) - distance, tick)

    # BUY YES must stay below the current YES ask. BUY NO maps to a YES-side ask of
    # 1 - no_quote and must stay above the current YES bid to remain maker-only.
    maker_ok = bool(yes_quote < ask and (ONE - no_quote) > bid)
    price_band_ok = bool(probe.MIN_PRICE <= yes_quote <= probe.MAX_PRICE and probe.MIN_PRICE <= no_quote <= probe.MAX_PRICE)
    yes_distance = mid - yes_quote
    no_distance = (ONE - mid) - no_quote
    reward_band_ok = bool(yes_distance <= D("0.10") and no_distance <= D("0.10") and yes_distance >= D("0") and no_distance >= D("0"))

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
        max_loss=LARGE_RESCUE_BUDGET,
        increment=tick,
    )
    sd = standing.as_dict()
    yes_route = sd.get("yes_fill_route") or {}
    no_route = sd.get("no_fill_route") or {}
    required_rescue = max(
        D(str(yes_route.get("loss_at_limit") or yes_route.get("loss_at_current") or "999")),
        D(str(no_route.get("loss_at_limit") or no_route.get("loss_at_current") or "999")),
    )

    return {
        "distance": str(distance),
        "yes_quote": str(yes_quote),
        "no_quote": str(no_quote),
        "yes_distance": str(yes_distance),
        "no_distance": str(no_distance),
        "maker_ok": maker_ok,
        "price_band_ok": price_band_ok,
        "reward_band_ok": reward_band_ok,
        "locked_edge": str(locked_edge),
        "required_rescue_loss": str(required_rescue),
        "fits_050": required_rescue <= D("0.50"),
        "fits_075": required_rescue <= D("0.75"),
        "fits_100": required_rescue <= D("1.00"),
        "yes_fill_route": yes_route,
        "no_fill_route": no_route,
    }


async def run() -> None:
    if RUN_SECONDS <= 0 or POLL_SECONDS <= 0:
        raise RuntimeError("shadow duration and poll interval must be positive")

    counts = {
        str(d): {str(b): 0 for b in BUDGETS}
        for d in DISTANCES
    }
    samples = 0
    zero_competition = 0
    last_minute: int | None = None
    deadline = time.monotonic() + RUN_SECONDS
    emit("START", run_seconds=RUN_SECONDS, poll_seconds=POLL_SECONDS, distances=[str(x) for x in DISTANCES], budgets=[str(x) for x in BUDGETS], read_only=True, order_entry=False)

    while time.monotonic() < deadline:
        try:
            event = await probe.base.discover_btc5m()
            if event is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            reward = await probe._reward_snapshot(event.ticker)
            if reward is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            row = reward["event"]
            makers = _maker_count(row)
            pool = str(row.get("daily_pool_usd") or row.get("dailyPoolUsd") or "")
            bid, bid_size, ask, ask_size = await probe.base.fresh_top(event.symbol)
            bid = D(str(bid)); bid_size = D(str(bid_size)); ask = D(str(ask)); ask_size = D(str(ask_size))
            tick = D(str(event.price_increment))
            geometries = [_quote_geometry(bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, tick=tick, distance=d) for d in DISTANCES]
            samples += 1
            if makers == 0:
                zero_competition += 1
            for g in geometries:
                base_ok = bool(g["maker_ok"] and g["price_band_ok"] and g["reward_band_ok"] and D(str(g["locked_edge"])) > D("0"))
                if not base_ok:
                    continue
                req = D(str(g["required_rescue_loss"]))
                for b in BUDGETS:
                    if req <= b:
                        counts[g["distance"]][str(b)] += 1

            minute = int(time.time() // 60)
            if minute != last_minute:
                last_minute = minute
                emit(
                    "MINUTE_GEOMETRY",
                    ticker=event.ticker,
                    symbol=event.symbol,
                    seconds_to_expiry=round(event.seconds_to_expiry, 3),
                    bid=str(bid),
                    ask=str(ask),
                    spread=str(ask - bid),
                    pool_usd=pool,
                    qualifying_maker_count=makers,
                    geometries=geometries,
                    read_only=True,
                    order_entry=False,
                )
        except Exception as exc:
            emit("SAMPLE_ERROR", error_type=type(exc).__name__, error_message=str(exc)[:240], read_only=True, order_entry=False)
        await asyncio.sleep(POLL_SECONDS)

    denom = max(samples, 1)
    emit(
        "SUMMARY",
        samples=samples,
        zero_competition_samples=zero_competition,
        zero_competition_pct=round(100.0 * zero_competition / denom, 3),
        feasible_counts=counts,
        feasible_pct={d: {b: round(100.0 * n / denom, 3) for b, n in by_budget.items()} for d, by_budget in counts.items()},
        read_only=True,
        order_entry=False,
        realized_pnl=None,
        rewards_realized=None,
    )


if __name__ == "__main__":
    asyncio.run(run())

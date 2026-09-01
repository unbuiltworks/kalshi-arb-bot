"""Read-only BTC5 q10 liquidity-reward safety frontier.

Simultaneously shadows several quote distances from Gemini midpoint using one hot
Gemini depth cache and one heartbeat-aware Coinbase BTC guard. The goal is to find
the tightest quote distance that can maintain a comfortable reward-uptime margin
without unguarded cross proxies or widening the existing $0.50 one-sided rescue cap.

Gemini does not publish the exact quadratic spread-weight function, so this module
never invents a reward-dollar estimate. It reports the empirical uptime/risk frontier
for each distance; actual reward dollars still require Gemini's daily summary/payout.

No private endpoints and no order mutation.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal as D, ROUND_CEILING
import json
import os
import time
from typing import Any, Mapping

from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_q10_maxwidth_spotguard_shadow_v2 as v2
from src.btc_spot_toxicity_guard_v2_shadow import BtcSpotGuardV2Shadow
from src.gemini_prediction_book_cache import GeminiTopCache

ONE = D("1")
RUN_SECONDS = float(os.getenv("GEMINI_Q10_FRONTIER_SECONDS", "1200"))
LOOP_SECONDS = float(os.getenv("GEMINI_Q10_FRONTIER_LOOP_SECONDS", "0.05"))
CALM_REPOST_SECONDS = float(os.getenv("GEMINI_Q10_FRONTIER_CALM_REPOST_SECONDS", "0.50"))
DISTANCES = tuple(
    D(x.strip())
    for x in os.getenv("GEMINI_Q10_FRONTIER_DISTANCES", "0.09,0.08,0.07,0.06,0.05,0.04,0.03,0.02").split(",")
    if x.strip()
)
MAX_RESCUE_LOSS = D(os.getenv("GEMINI_Q10_SPOTGUARD_MAX_RESCUE_LOSS", "0.50"))


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_Q10_REWARD_FRONTIER="
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


def target(mid: D, tick: D, distance: D) -> tuple[D, D]:
    return _ceil_tick(mid - distance, tick), _ceil_tick((ONE - mid) - distance, tick)


@dataclass
class Band:
    distance: D
    yes_quote: D | None = None
    no_quote: D | None = None
    calm_since: float | None = None
    posts: int = 0
    pulls: int = 0
    spot_pulls: int = 0
    recenter_pulls: int = 0
    crosses: int = 0
    guarded_crosses: int = 0
    unguarded_crosses: int = 0
    eligible_seconds: float = 0.0
    qualified_seconds: float = 0.0
    minute_total: int = 0
    minute_qualified: int = 0

    def reset_quote(self) -> None:
        self.yes_quote = None
        self.no_quote = None
        self.calm_since = None

    def summary(self) -> dict[str, Any]:
        uptime = 0.0 if self.eligible_seconds <= 0 else 100.0 * self.qualified_seconds / self.eligible_seconds
        minute_uptime = 0.0 if self.minute_total <= 0 else 100.0 * self.minute_qualified / self.minute_total
        return {
            "distance": str(self.distance),
            "continuous_qualified_pct": round(uptime, 3),
            "eligible_seconds": round(self.eligible_seconds, 3),
            "qualified_seconds": round(self.qualified_seconds, 3),
            "minute_qualified": self.minute_qualified,
            "minute_total": self.minute_total,
            "minute_qualified_pct": round(minute_uptime, 3),
            "posts": self.posts,
            "pulls": self.pulls,
            "spot_pulls": self.spot_pulls,
            "recenter_pulls": self.recenter_pulls,
            "crosses": self.crosses,
            "guarded_crosses": self.guarded_crosses,
            "unguarded_crosses": self.unguarded_crosses,
        }


def evaluate(*, yes_quote: D, no_quote: D, bid: D, bid_size: D, ask: D,
             ask_size: D, tick: D, max_distance: D) -> dict[str, Any]:
    # Reuse corrected V2 eligibility: reward band is independent of maker-rebate band.
    return v2.evaluate_v2(
        yes_quote=yes_quote,
        no_quote=no_quote,
        bid=bid,
        bid_size=bid_size,
        ask=ask,
        ask_size=ask_size,
        tick=tick,
        max_distance=max_distance,
    )


async def run() -> None:
    if RUN_SECONDS <= 0 or LOOP_SECONDS <= 0 or not DISTANCES:
        raise RuntimeError("invalid frontier configuration")
    if any(d <= D("0") or d > D("0.10") for d in DISTANCES):
        raise RuntimeError("frontier distances must be in (0, 0.10]")

    guard = BtcSpotGuardV2Shadow(window_ms=2000, stale_ms=1500, pull_move_bps=D("5"))
    spot_stop = asyncio.Event()
    spot_task = asyncio.create_task(guard.run_coinbase(spot_stop))
    bands = {d: Band(d) for d in DISTANCES}

    cache: GeminiTopCache | None = None
    cache_stop: asyncio.Event | None = None
    cache_task: asyncio.Task | None = None
    symbol: str | None = None
    ticker: str | None = None
    last_book_seq = -1
    last_minute: int | None = None
    reward: dict[str, Any] | None = None
    last_reward_refresh = 0.0
    last_discovery = 0.0
    event = None
    last_loop = time.monotonic()
    deadline = last_loop + RUN_SECONDS

    emit(
        "START",
        run_seconds=RUN_SECONDS,
        loop_seconds=LOOP_SECONDS,
        distances=[str(d) for d in DISTANCES],
        max_rescue_loss=str(MAX_RESCUE_LOSS),
        reward_dollar_estimate=False,
        read_only=True,
        order_entry=False,
    )

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            dt = min(max(0.0, now - last_loop), 0.25)
            last_loop = now
            try:
                if event is None or now - last_discovery >= 1.0:
                    event = await probe.base.discover_btc5m()
                    last_discovery = now
                if event is None:
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                if symbol != event.symbol:
                    if cache_stop is not None:
                        cache_stop.set()
                    if cache_task is not None:
                        cache_task.cancel()
                        await asyncio.gather(cache_task, return_exceptions=True)
                    symbol, ticker = event.symbol, event.ticker
                    for band in bands.values():
                        band.reset_quote()
                    cache = GeminiTopCache(symbol, stale_ms=750)
                    cache_stop = asyncio.Event()
                    cache_task = asyncio.create_task(cache.run(cache_stop))
                    last_book_seq = -1
                    reward = await probe._reward_snapshot(event.ticker)
                    last_reward_refresh = time.monotonic()
                    emit(
                        "NEW_MARKET",
                        ticker=ticker,
                        symbol=symbol,
                        seconds_to_expiry=round(event.seconds_to_expiry, 3),
                        read_only=True,
                        order_entry=False,
                    )

                assert cache is not None
                try:
                    snap = await cache.wait_fresh(timeout=0.20, max_age_ms=500)
                except Exception:
                    for band in bands.values():
                        if band.yes_quote is not None:
                            band.pulls += 1
                            band.reset_quote()
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                now = time.monotonic()
                if now - last_reward_refresh >= 5.0:
                    reward = await probe._reward_snapshot(event.ticker)
                    last_reward_refresh = now
                if reward is None:
                    for band in bands.values():
                        if band.yes_quote is not None:
                            band.pulls += 1
                            band.reset_quote()
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                row = reward["event"]
                cfg = reward["config"]
                makers = _maker_count(row)
                pool = str(row.get("daily_pool_usd") or row.get("dailyPoolUsd") or "")
                max_distance = D(str(cfg.get("max_spread_cents") or 10)) / D("100")
                spot = guard.decision()
                bid, bid_size, ask, ask_size = snap.bid, snap.bid_size, snap.ask, snap.ask_size
                tick = D(str(event.price_increment))
                mid = (bid + ask) / D("2")
                feed_calm = bool(spot.ready and not spot.dangerous)

                new_book = snap.sequence != last_book_seq
                for band in bands.values():
                    band.eligible_seconds += dt
                    if feed_calm:
                        band.calm_since = band.calm_since or now
                    else:
                        band.calm_since = None

                    if band.yes_quote is not None and band.no_quote is not None and new_book:
                        current = evaluate(
                            yes_quote=band.yes_quote,
                            no_quote=band.no_quote,
                            bid=bid,
                            bid_size=bid_size,
                            ask=ask,
                            ask_size=ask_size,
                            tick=tick,
                            max_distance=max_distance,
                        )
                        if current["yes_cross"] or current["no_cross"]:
                            band.crosses += 1
                            if feed_calm:
                                band.unguarded_crosses += 1
                            else:
                                band.guarded_crosses += 1
                            band.reset_quote()

                    if band.yes_quote is not None and band.no_quote is not None:
                        current = evaluate(
                            yes_quote=band.yes_quote,
                            no_quote=band.no_quote,
                            bid=bid,
                            bid_size=bid_size,
                            ask=ask,
                            ask_size=ask_size,
                            tick=tick,
                            max_distance=max_distance,
                        )
                        if not feed_calm:
                            band.pulls += 1
                            band.spot_pulls += 1
                            band.reset_quote()
                        elif not current["qualified_safe"]:
                            band.pulls += 1
                            band.recenter_pulls += 1
                            band.reset_quote()

                    if (
                        band.yes_quote is None
                        and band.calm_since is not None
                        and now - band.calm_since >= CALM_REPOST_SECONDS
                    ):
                        y, n = target(mid, tick, band.distance)
                        candidate = evaluate(
                            yes_quote=y,
                            no_quote=n,
                            bid=bid,
                            bid_size=bid_size,
                            ask=ask,
                            ask_size=ask_size,
                            tick=tick,
                            max_distance=max_distance,
                        )
                        if candidate["qualified_safe"]:
                            band.yes_quote, band.no_quote = y, n
                            band.posts += 1

                    qualified = False
                    if band.yes_quote is not None and band.no_quote is not None and feed_calm:
                        current = evaluate(
                            yes_quote=band.yes_quote,
                            no_quote=band.no_quote,
                            bid=bid,
                            bid_size=bid_size,
                            ask=ask,
                            ask_size=ask_size,
                            tick=tick,
                            max_distance=max_distance,
                        )
                        qualified = bool(current["qualified_safe"])
                    if qualified:
                        band.qualified_seconds += dt

                last_book_seq = snap.sequence
                minute = int(time.time() // 60)
                if minute != last_minute:
                    last_minute = minute
                    rows = []
                    for band in bands.values():
                        band.minute_total += 1
                        qualified = False
                        if band.yes_quote is not None and band.no_quote is not None and feed_calm:
                            current = evaluate(
                                yes_quote=band.yes_quote,
                                no_quote=band.no_quote,
                                bid=bid,
                                bid_size=bid_size,
                                ask=ask,
                                ask_size=ask_size,
                                tick=tick,
                                max_distance=max_distance,
                            )
                            qualified = bool(current["qualified_safe"])
                        if qualified:
                            band.minute_qualified += 1
                        rows.append(band.summary())
                    emit(
                        "FRONTIER_SNAPSHOT",
                        ticker=ticker,
                        bid=str(bid),
                        ask=str(ask),
                        spot=asdict(spot),
                        pool_usd=pool,
                        qualifying_maker_count=makers,
                        bands=rows,
                        read_only=True,
                        order_entry=False,
                    )
            except Exception as exc:
                emit(
                    "LOOP_ERROR",
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:240],
                    read_only=True,
                    order_entry=False,
                )
            await asyncio.sleep(LOOP_SECONDS)
    finally:
        if cache_stop is not None:
            cache_stop.set()
        if cache_task is not None:
            cache_task.cancel()
            await asyncio.gather(cache_task, return_exceptions=True)
        spot_stop.set()
        spot_task.cancel()
        await asyncio.gather(spot_task, return_exceptions=True)

    rows = [band.summary() for band in bands.values()]
    # A deployment candidate must be comfortably above the formal 50% threshold and
    # show no unguarded cross proxies in this sample. 65% is deliberately conservative.
    candidates = [
        row for row in rows
        if row["continuous_qualified_pct"] >= 65.0 and row["unguarded_crosses"] == 0
    ]
    tightest = min(candidates, key=lambda row: D(row["distance"]), default=None)
    emit(
        "SUMMARY",
        bands=rows,
        conservative_candidate=tightest,
        candidate_rule="continuous>=65pct_and_unguarded_crosses=0",
        reward_dollar_estimate=False,
        realized_pnl=None,
        rewards_realized=None,
        read_only=True,
        order_entry=False,
    )


if __name__ == "__main__":
    asyncio.run(run())

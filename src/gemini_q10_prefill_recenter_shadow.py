"""Read-only BTC5 q10 pre-fill recenter shadow.

Purpose: measure whether V5.2's main practical bottleneck -- a standing rescue bound
breaking after a small book move -- can be solved by cancel/recenter/repost *before*
any fill, without widening loss limits.

This module NEVER places or cancels orders. It only uses public Gemini market/reward
endpoints and simulates a q10 pair that is re-anchored whenever the fixed pair would
lose its <=30c standing rescue invariant. It records minute-snapshot eligibility so
we can judge whether a bounded live recenter implementation could plausibly satisfy
Gemini's liquidity-reward uptime requirement.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal as D
import json
import os
import time
from typing import Any, Mapping

from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_q10_production_executor_v5 as v5
from src import gemini_q10_production_executor_v5_1 as v51
from src import gemini_q10_rescue_policy as rescue

Q = D("10")
ZERO = D("0")
RUN_SECONDS = float(os.getenv("GEMINI_Q10_RECENTER_SHADOW_SECONDS", "1800"))
POLL_SECONDS = float(os.getenv("GEMINI_Q10_RECENTER_SHADOW_POLL_SECONDS", "1.0"))
MAX_RECENTERS = int(os.getenv("GEMINI_Q10_RECENTER_SHADOW_MAX_RECENTERS", "1000"))


@dataclass
class SimState:
    symbol: str | None = None
    ticker: str | None = None
    anchor_yes: D | None = None
    anchor_no: D | None = None
    anchor_at: float | None = None
    recenters: int = 0
    observed_seconds: float = 0.0
    dynamic_qualified_seconds: float = 0.0
    static_qualified_seconds: float = 0.0
    zero_competition_seconds: float = 0.0
    dynamic_snapshot_count: int = 0
    static_snapshot_count: int = 0
    total_snapshot_opportunities: int = 0
    last_snapshot_minute: int | None = None
    last_emit: float = 0.0
    initial_yes: D | None = None
    initial_no: D | None = None


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_Q10_RECENTER_SHADOW="
        + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str),
        flush=True,
    )


def _maker_count(row: Mapping[str, Any]) -> int | None:
    raw = row.get("qualifying_maker_count") if "qualifying_maker_count" in row else row.get("qualifyingMakerCount")
    try:
        return int(raw)
    except Exception:
        return None


def _pool(row: Mapping[str, Any]) -> str:
    return str(row.get("daily_pool_usd") or row.get("dailyPoolUsd") or "")


def _model_ok(model: Mapping[str, D]) -> bool:
    # Match V5.1 admission economics; this is read-only and does not relax live gates.
    return v51.entry_model_ok(model)


def _standing(yes_quote: D, no_quote: D, *, bid: D, bid_size: D, ask: D, ask_size: D, increment: D):
    return rescue.standing_rescue_check(
        yes_quote=yes_quote,
        no_quote=no_quote,
        quantity=Q,
        yes_bid=bid,
        yes_bid_size=bid_size,
        yes_ask=ask,
        yes_ask_size=ask_size,
        max_loss=v5.max_rescue_loss(),
        increment=increment,
    )


def _spread_qualified(yes_quote: D, no_quote: D, *, bid: D, ask: D, max_distance: D) -> bool:
    mid = (bid + ask) / D("2")
    return bool(
        (mid - yes_quote) <= max_distance
        and ((D("1") - mid) - no_quote) <= max_distance
        and probe.MIN_PRICE <= yes_quote <= probe.MAX_PRICE
        and probe.MIN_PRICE <= no_quote <= probe.MAX_PRICE
    )


async def sample(state: SimState) -> None:
    event = await probe.base.discover_btc5m()
    if event is None:
        return
    reward = await probe._reward_snapshot(event.ticker)
    if reward is None:
        return
    row = reward["event"]
    cfg = reward["config"]
    makers = _maker_count(row)
    max_distance = D(str(cfg.get("max_spread_cents") or 10)) / D("100")

    bid, bid_size, ask, ask_size = await probe.base.fresh_top(event.symbol)
    bid = D(str(bid)); bid_size = D(str(bid_size)); ask = D(str(ask)); ask_size = D(str(ask_size))
    current = probe._model(bid, ask)
    current_ok = _model_ok(current)
    current_standing = _standing(
        D(str(current["yes_quote"])), D(str(current["no_quote"])),
        bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, increment=event.price_increment,
    )
    current_safe = bool(current_ok and current_standing.safe)

    now = time.monotonic()
    if state.symbol != event.symbol:
        state.symbol = event.symbol
        state.ticker = event.ticker
        state.anchor_yes = None
        state.anchor_no = None
        state.anchor_at = None
        state.initial_yes = None
        state.initial_no = None
        emit(
            "NEW_MARKET",
            ticker=event.ticker,
            symbol=event.symbol,
            seconds_to_expiry=round(event.seconds_to_expiry, 3),
            pool_usd=_pool(row),
            qualifying_maker_count=makers,
        )

    if state.anchor_yes is None and current_safe:
        state.anchor_yes = D(str(current["yes_quote"]))
        state.anchor_no = D(str(current["no_quote"]))
        state.anchor_at = now
        if state.initial_yes is None:
            state.initial_yes = state.anchor_yes
            state.initial_no = state.anchor_no
        emit(
            "VIRTUAL_ANCHOR",
            ticker=event.ticker,
            yes_quote=state.anchor_yes,
            no_quote=state.anchor_no,
            locked_edge=current["locked_edge"],
            standing=current_standing.as_dict(),
            pool_usd=_pool(row),
            qualifying_maker_count=makers,
        )

    dynamic_safe = False
    dynamic_spread_ok = False
    if state.anchor_yes is not None and state.anchor_no is not None:
        dynamic_standing = _standing(
            state.anchor_yes, state.anchor_no,
            bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, increment=event.price_increment,
        )
        dynamic_spread_ok = _spread_qualified(
            state.anchor_yes, state.anchor_no, bid=bid, ask=ask, max_distance=max_distance,
        )
        dynamic_safe = bool(dynamic_standing.safe and dynamic_spread_ok)
        if not dynamic_safe and current_safe and state.recenters < MAX_RECENTERS:
            old_yes, old_no = state.anchor_yes, state.anchor_no
            state.anchor_yes = D(str(current["yes_quote"]))
            state.anchor_no = D(str(current["no_quote"]))
            state.anchor_at = now
            state.recenters += 1
            dynamic_standing = current_standing
            dynamic_spread_ok = _spread_qualified(
                state.anchor_yes, state.anchor_no, bid=bid, ask=ask, max_distance=max_distance,
            )
            dynamic_safe = bool(dynamic_standing.safe and dynamic_spread_ok)
            emit(
                "VIRTUAL_RECENTER",
                ticker=event.ticker,
                recenter_no=state.recenters,
                old_yes=old_yes,
                old_no=old_no,
                new_yes=state.anchor_yes,
                new_no=state.anchor_no,
                locked_edge=current["locked_edge"],
                standing=dynamic_standing.as_dict(),
                pool_usd=_pool(row),
                qualifying_maker_count=makers,
            )

    static_safe = False
    static_spread_ok = False
    if state.initial_yes is not None and state.initial_no is not None:
        static_standing = _standing(
            state.initial_yes, state.initial_no,
            bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size, increment=event.price_increment,
        )
        static_spread_ok = _spread_qualified(
            state.initial_yes, state.initial_no, bid=bid, ask=ask, max_distance=max_distance,
        )
        static_safe = bool(static_standing.safe and static_spread_ok)

    dt = POLL_SECONDS
    state.observed_seconds += dt
    if makers == 0:
        state.zero_competition_seconds += dt
    if dynamic_safe:
        state.dynamic_qualified_seconds += dt
    if static_safe:
        state.static_qualified_seconds += dt

    wall_minute = int(time.time() // 60)
    if wall_minute != state.last_snapshot_minute:
        state.last_snapshot_minute = wall_minute
        state.total_snapshot_opportunities += 1
        if dynamic_safe:
            state.dynamic_snapshot_count += 1
        if static_safe:
            state.static_snapshot_count += 1
        emit(
            "MINUTE_SNAPSHOT_PROXY",
            ticker=event.ticker,
            dynamic_qualified=dynamic_safe,
            static_qualified=static_safe,
            dynamic_snapshots=state.dynamic_snapshot_count,
            static_snapshots=state.static_snapshot_count,
            total_snapshot_opportunities=state.total_snapshot_opportunities,
            recenters=state.recenters,
            qualifying_maker_count=makers,
            pool_usd=_pool(row),
        )

    if now - state.last_emit >= 15.0:
        state.last_emit = now
        emit(
            "HEARTBEAT",
            ticker=event.ticker,
            symbol=event.symbol,
            seconds_to_expiry=round(event.seconds_to_expiry, 3),
            bid=bid,
            ask=ask,
            current_yes=current["yes_quote"],
            current_no=current["no_quote"],
            current_locked_edge=current["locked_edge"],
            anchor_yes=state.anchor_yes,
            anchor_no=state.anchor_no,
            dynamic_safe=dynamic_safe,
            static_safe=static_safe,
            recenters=state.recenters,
            qualifying_maker_count=makers,
            pool_usd=_pool(row),
            read_only=True,
            order_entry=False,
        )


async def run() -> None:
    if RUN_SECONDS <= 0 or POLL_SECONDS <= 0:
        raise RuntimeError("shadow duration and poll interval must be positive")
    state = SimState()
    deadline = time.monotonic() + RUN_SECONDS
    emit(
        "START",
        run_seconds=RUN_SECONDS,
        poll_seconds=POLL_SECONDS,
        max_rescue_loss=v5.max_rescue_loss(),
        read_only=True,
        order_entry=False,
    )
    while time.monotonic() < deadline:
        try:
            await sample(state)
        except Exception as exc:
            emit("SAMPLE_ERROR", error_type=type(exc).__name__, error_message=str(exc)[:240], order_entry=False)
        await asyncio.sleep(POLL_SECONDS)

    obs = max(state.observed_seconds, 0.001)
    snaps = max(state.total_snapshot_opportunities, 1)
    emit(
        "SUMMARY",
        observed_seconds=round(state.observed_seconds, 3),
        dynamic_qualified_seconds=round(state.dynamic_qualified_seconds, 3),
        static_qualified_seconds=round(state.static_qualified_seconds, 3),
        dynamic_time_pct=round(100.0 * state.dynamic_qualified_seconds / obs, 3),
        static_time_pct=round(100.0 * state.static_qualified_seconds / obs, 3),
        zero_competition_pct=round(100.0 * state.zero_competition_seconds / obs, 3),
        dynamic_snapshot_count=state.dynamic_snapshot_count,
        static_snapshot_count=state.static_snapshot_count,
        total_snapshot_opportunities=state.total_snapshot_opportunities,
        dynamic_snapshot_pct=round(100.0 * state.dynamic_snapshot_count / snaps, 3),
        static_snapshot_pct=round(100.0 * state.static_snapshot_count / snaps, 3),
        recenters=state.recenters,
        read_only=True,
        order_entry=False,
        realized_pnl=None,
        rewards_realized=None,
    )


if __name__ == "__main__":
    asyncio.run(run())

"""Globally coordinated parallel-per-asset live crypto campaign.

Guarantees:
1. At most one unresolved trade per BTC/ETH/SOL/XRP lane.
2. Shared and per-venue capital are reserved before an order-capable child starts.
3. A hard halt in ANY lane sets a global stop event and terminates every other
   order-capable child immediately.
4. Completed trades plus active trade slots never exceed the bounded campaign cap.

Risk gates are unchanged: q=1, >=$0.03 modeled profit, >=10bp double boundary,
50ms survival, <=50ms quote skew.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import sys
from typing import Any

import httpx

from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_crypto_parallel_campaign as p
from src import gemini_kalshi_sol_live_canary_v2 as v2

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_parallel_campaign_safe")


class GlobalParallelStop(RuntimeError):
    pass


def active_trade_slots(state: p.ParallelState) -> int:
    return sum(1 for lane in state.lanes.values() if lane.active_run_id is not None)


def trade_slot_available(state: p.ParallelState) -> bool:
    return state.completed_total + active_trade_slots(state) < p.MAX_COMPLETED_TRADES


async def stop_registered_children(
    registry: dict[str, asyncio.subprocess.Process],
    registry_lock: asyncio.Lock,
) -> None:
    async with registry_lock:
        items = list(registry.items())
        registry.clear()
    if items:
        await asyncio.gather(*(base._stop_child(proc) for _, proc in items), return_exceptions=True)
        logger.critical(
            "CRYPTO_PARALLEL_GLOBAL_CHILD_STOP assets=%s child_count=%s",
            ",".join(asset for asset, _ in items), len(items),
        )


async def _obtain_asset_execution(
    run_id: str,
    asset: str,
    arm_deadline: datetime,
    stop_event: asyncio.Event,
    registry: dict[str, asyncio.subprocess.Process],
    registry_lock: asyncio.Lock,
) -> dict[str, Any]:
    arm, claim, result_path = v2._paths(run_id)
    if result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8"))
    if claim.is_file():
        raise RuntimeError(f"attempt claim exists without result for {run_id}")
    if stop_event.is_set():
        raise GlobalParallelStop("global parallel stop already set")

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m", "src.gemini_kalshi_crypto_campaign_child_reliable",
        env=p._asset_child_env(run_id, asset),
    )
    async with registry_lock:
        if stop_event.is_set():
            await base._stop_child(process)
            raise GlobalParallelStop("global parallel stop raced child start")
        registry[asset] = process

    wait_task = asyncio.create_task(process.wait())
    try:
        while base._utcnow() <= arm_deadline:
            if result_path.is_file():
                return json.loads(result_path.read_text(encoding="utf-8"))
            if stop_event.is_set():
                raise GlobalParallelStop(f"global stop while waiting for {run_id}")
            if wait_task.done():
                raise RuntimeError(f"asset child exited {wait_task.result()} before result run_id={run_id}")
            await asyncio.sleep(min(base.CHILD_RESULT_POLL_S, 0.05))
        raise TimeoutError(f"campaign arm deadline reached before execution for {run_id}")
    finally:
        async with registry_lock:
            if registry.get(asset) is process:
                registry.pop(asset, None)
        await base._stop_child(process)
        if not wait_task.done():
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
        if arm.is_file() and not claim.is_file():
            try:
                arm.unlink()
            except FileNotFoundError:
                pass


async def _lane_loop(
    state: p.ParallelState,
    asset: str,
    arm_deadline: datetime,
    http: httpx.AsyncClient,
    state_lock: asyncio.Lock,
    stop_event: asyncio.Event,
    registry: dict[str, asyncio.subprocess.Process],
    registry_lock: asyncio.Lock,
) -> None:
    lane = state.lanes[asset]
    while base._utcnow() <= arm_deadline and not stop_event.is_set():
        reservation_key: str | None = None
        async with state_lock:
            if state.halted or stop_event.is_set() or state.completed_total >= p.MAX_COMPLETED_TRADES:
                return
            if state.execution_attempts_total >= p.MAX_EXECUTION_ATTEMPTS:
                lane.status = "COMPLETE_MAX_EXECUTION_ATTEMPTS"
                p._write_state(state)
                return

            if not trade_slot_available(state):
                lane.status = "WAITING_TRADE_SLOT"
                p._write_state(state)
                should_wait = True
            else:
                enough_global = state.capital.available >= p.RESERVE_PER_ATTEMPT
                enough_venues = state.venues.can_reserve_pair(p.VENUE_RESERVE_PER_ATTEMPT)
                if not enough_global or not enough_venues:
                    lane.status = "WAITING_CAPITAL"
                    p._write_state(state)
                    should_wait = True
                else:
                    should_wait = False
                    lane.execution_attempts += 1
                    state.execution_attempts_total += 1
                    run_id = p._run_id(state.campaign_id, asset, lane.execution_attempts)
                    reservation_key = run_id
                    state.capital.reserve(reservation_key, p.RESERVE_PER_ATTEMPT)
                    try:
                        state.venues.reserve_pair(reservation_key, p.VENUE_RESERVE_PER_ATTEMPT)
                    except Exception:
                        state.capital.release(reservation_key)
                        raise
                    lane.reserved_usd = p.RESERVE_PER_ATTEMPT
                    lane.active_run_id = run_id
                    lane.status = "WAITING_EXECUTION"
                    p._write_state(state)
                    logger.warning(
                        "CRYPTO_PARALLEL_SAFE_LANE_ARMED asset=%s run_id=%s active_slots=%s completed=%s "
                        "pair_reserved=%s venue_reserved_each=%s global_available=%s gemini_free=%s kalshi_free=%s",
                        asset, run_id, active_trade_slots(state), state.completed_total,
                        p.RESERVE_PER_ATTEMPT, p.VENUE_RESERVE_PER_ATTEMPT,
                        state.capital.available, state.venues.free_for("gemini"), state.venues.free_for("kalshi"),
                    )

        if should_wait:
            await asyncio.sleep(0.10)
            continue

        try:
            record = await _obtain_asset_execution(
                run_id, asset, arm_deadline, stop_event, registry, registry_lock,
            )
            result = record.get("result") or {}

            if reliable._benign_primary_miss(result):
                async with state_lock:
                    state.capital.release(reservation_key)
                    state.venues.release(reservation_key)
                    lane.reserved_usd = D("0")
                    lane.active_run_id = None
                    lane.status = "IDLE"
                    p._write_state(state)
                logger.warning(
                    "CRYPTO_PARALLEL_SAFE_BENIGN_NO_FILL asset=%s run_id=%s continuing=true",
                    asset, run_id,
                )
                continue

            if str(result.get("outcome") or "") != "BOTH_FILLED" or bool(result.get("hard_halt_required")):
                try:
                    await base._emit_execution_events(http, run_id, record)
                except Exception:
                    pass
                raise RuntimeError(f"unsafe live execution asset={asset} outcome={result.get('outcome')}")

            costs = await base._emit_execution_events(http, run_id, record)
            actual_capital = D(str(costs["capital"]))
            actual_venue = p._venue_costs(result)
            async with state_lock:
                state.capital.convert_to_deployed(reservation_key, actual_capital)
                state.venues.convert_to_deployed(reservation_key, actual_venue)
                lane.reserved_usd = D("0")
                lane.status = "WAITING_SETTLEMENT"
                p._write_state(state)

            gp = record.get("gemini_payload") or {}
            kp = record.get("kalshi_payload") or {}
            settlement = await base._official_settlement(
                http,
                gemini_symbol=str(gp.get("symbol") or ""),
                kalshi_ticker=str(kp.get("ticker") or ""),
            )
            net_pnl = await base._emit_realized_event(
                http,
                run_id=run_id,
                record=record,
                costs=costs,
                settlement=settlement,
            )

            if not bool(settlement["venue_outcomes_agree"]):
                raise RuntimeError(f"settlement disagreement asset={asset} run_id={run_id}")

            async with state_lock:
                state.capital.settle(actual_capital)
                state.venues.settle(actual_venue)
                lane.completed_trades += 1
                state.completed_total += 1
                lane.active_run_id = None
                lane.status = "IDLE"
                p._write_state(state)
            logger.warning(
                "CRYPTO_PARALLEL_SAFE_REALIZED asset=%s run_id=%s net_pnl=%s capital=%s completed_total=%s active_slots=%s",
                asset, run_id, net_pnl, actual_capital, state.completed_total, active_trade_slots(state),
            )

        except GlobalParallelStop:
            async with state_lock:
                if reservation_key and reservation_key in state.capital.reservations:
                    state.capital.release(reservation_key)
                if reservation_key and reservation_key in state.venues.reservations:
                    state.venues.release(reservation_key)
                lane.reserved_usd = D("0")
                lane.active_run_id = None
                lane.status = "STOPPED_GLOBAL_HALT"
                p._write_state(state)
            return

        except Exception as exc:
            async with state_lock:
                if reservation_key and reservation_key in state.capital.reservations:
                    state.capital.release(reservation_key)
                if reservation_key and reservation_key in state.venues.reservations:
                    state.venues.release(reservation_key)
                lane.reserved_usd = D("0")
                lane.status = "HALTED"
                state.halted = True
                state.halt_reason = f"{asset}:{type(exc).__name__}:{str(exc)[:300]}"
                stop_event.set()
                p._write_state(state)
            logger.exception("CRYPTO_PARALLEL_SAFE_GLOBAL_HALT asset=%s run_id=%s", asset, run_id)
            await stop_registered_children(registry, registry_lock)
            return


async def run() -> None:
    campaign_id = base._clean_campaign_id(os.getenv(base.CAMPAIGN_ID_ENV, ""))
    deadline_raw = os.getenv("SOL_CANARY_V2_ARM_NOT_AFTER_UTC", "").strip()
    if not deadline_raw:
        raise RuntimeError("SOL_CANARY_V2_ARM_NOT_AFTER_UTC is required")
    arm_deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    if base._utcnow() > arm_deadline:
        raise RuntimeError("campaign arm deadline already expired")
    if os.getenv(v2.ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError("parallel campaign requires explicit arm")
    if os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() != "YES":
        raise PermissionError("parallel campaign requires live probe environment gate")
    if not D("0") < p.MAX_TOTAL_RESERVED <= D("5"):
        raise ValueError("invalid global hard budget")
    if not D("0") < p.RESERVE_PER_ATTEMPT <= D("1"):
        raise ValueError("invalid pair reservation")
    if not D("0") < p.VENUE_RESERVE_PER_ATTEMPT <= D("1"):
        raise ValueError("invalid per-venue reservation")
    if not 1 <= p.MAX_COMPLETED_TRADES <= 20:
        raise ValueError("invalid max completed trades")
    if not p.MAX_COMPLETED_TRADES <= p.MAX_EXECUTION_ATTEMPTS <= 100:
        raise ValueError("invalid max execution attempts")

    venue_balances = await p._live_venue_balances()
    if venue_balances["gemini"] < p.VENUE_RESERVE_PER_ATTEMPT or venue_balances["kalshi"] < p.VENUE_RESERVE_PER_ATTEMPT:
        raise RuntimeError(f"insufficient venue balance for one reserved pair: {venue_balances}")

    state = p.ParallelState(campaign_id, p.MAX_TOTAL_RESERVED, venue_balances)
    state_lock = asyncio.Lock()
    registry_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    registry: dict[str, asyncio.subprocess.Process] = {}
    p._write_state(state)

    logger.warning(
        "CRYPTO_PARALLEL_SAFE_CAMPAIGN_ENABLED campaign_id=%s assets=%s one_unresolved_per_asset=true "
        "global_halt_kills_all_children=true trade_slots_reserved=true shared_capital_reservations=true "
        "per_venue_reservations=true hard_budget=%s reserve_per_attempt=%s venue_reserve_each=%s "
        "gemini_available=%s kalshi_available=%s min_profit=0.03 min_double_boundary_bps=10 "
        "execution_delay_ms=50 risk_gates_changed=false",
        campaign_id, ",".join(p.ASSETS), p.MAX_TOTAL_RESERVED, p.RESERVE_PER_ATTEMPT,
        p.VENUE_RESERVE_PER_ATTEMPT, venue_balances["gemini"], venue_balances["kalshi"],
    )

    async with httpx.AsyncClient(timeout=20.0) as http:
        try:
            await asyncio.gather(*[
                _lane_loop(
                    state, asset, arm_deadline, http, state_lock,
                    stop_event, registry, registry_lock,
                )
                for asset in p.ASSETS
            ])
        finally:
            stop_event.set()
            await stop_registered_children(registry, registry_lock)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()

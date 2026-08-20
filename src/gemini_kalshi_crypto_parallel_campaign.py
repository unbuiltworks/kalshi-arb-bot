"""Parallel-per-asset live campaign supervisor with capital reservations.

This module is intentionally not enabled by default. It runs up to one unresolved
trade per asset (BTC/ETH/SOL/XRP), while enforcing BOTH:

* a shared hard capital-at-risk budget across all unresolved pairs; and
* per-venue USD reservations so concurrent lanes cannot spend the same Gemini or
  Kalshi balance twice.

Settlement and dashboard realization remain mandatory before an asset lane can re-arm.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any

import httpx

from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_sol_live_canary_v2 as v2
from src.gemini_kalshi_live_service import _gemini_available_usd
from src.gemini_kalshi_probe_transport import KalshiV2Executor

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_parallel_campaign")
ASSETS = ("BTC", "ETH", "SOL", "XRP")
STATE_DIR = Path(os.getenv("CRYPTO_PARALLEL_STATE_DIR", "/data/crypto-parallel"))
RESERVE_PER_ATTEMPT = D(os.getenv("CRYPTO_PARALLEL_RESERVE_PER_ATTEMPT", "1"))
VENUE_RESERVE_PER_ATTEMPT = D(os.getenv("CRYPTO_PARALLEL_VENUE_RESERVE_PER_ATTEMPT", "1"))
MAX_TOTAL_RESERVED = D(os.getenv("CRYPTO_CANARY_HARD_BUDGET", "5"))
MAX_COMPLETED_TRADES = int(os.getenv("CRYPTO_CANARY_MAX_TRADES", "5"))
MAX_EXECUTION_ATTEMPTS = int(os.getenv("CRYPTO_CANARY_MAX_EXECUTION_ATTEMPTS", "25"))


@dataclass
class Lane:
    asset: str
    execution_attempts: int = 0
    completed_trades: int = 0
    reserved_usd: D = D("0")
    active_run_id: str | None = None
    status: str = "IDLE"


@dataclass
class CapitalBook:
    hard_budget: D
    unsettled_deployed: D = D("0")
    reservations: dict[str, D] = field(default_factory=dict)

    @property
    def reserved(self) -> D:
        return sum(self.reservations.values(), D("0"))

    @property
    def available(self) -> D:
        return self.hard_budget - self.unsettled_deployed - self.reserved

    def reserve(self, key: str, amount: D) -> None:
        if key in self.reservations:
            raise RuntimeError(f"duplicate reservation {key}")
        if amount <= 0 or amount > self.available:
            raise RuntimeError(f"capital reservation denied key={key} amount={amount} available={self.available}")
        self.reservations[key] = amount

    def release(self, key: str) -> None:
        self.reservations.pop(key, None)

    def convert_to_deployed(self, key: str, actual_capital: D) -> None:
        reserved = self.reservations.pop(key, None)
        if reserved is None:
            raise RuntimeError(f"missing reservation {key}")
        if actual_capital < 0 or actual_capital > reserved:
            raise RuntimeError(f"actual capital exceeds reservation key={key} actual={actual_capital} reserved={reserved}")
        self.unsettled_deployed += actual_capital

    def settle(self, actual_capital: D) -> None:
        if actual_capital < 0 or actual_capital > self.unsettled_deployed:
            raise RuntimeError(f"invalid settlement release actual={actual_capital} unsettled={self.unsettled_deployed}")
        self.unsettled_deployed -= actual_capital


@dataclass
class VenueCapitalBook:
    available_balances: dict[str, D]
    reservations: dict[str, dict[str, D]] = field(default_factory=dict)
    unsettled: dict[str, D] = field(default_factory=lambda: {"gemini": D("0"), "kalshi": D("0")})

    def reserved_for(self, venue: str) -> D:
        return sum((r.get(venue, D("0")) for r in self.reservations.values()), D("0"))

    def free_for(self, venue: str) -> D:
        return self.available_balances[venue] - self.unsettled.get(venue, D("0")) - self.reserved_for(venue)

    def can_reserve_pair(self, amount_each: D) -> bool:
        return all(self.free_for(v) >= amount_each for v in ("gemini", "kalshi"))

    def reserve_pair(self, key: str, amount_each: D) -> None:
        if key in self.reservations:
            raise RuntimeError(f"duplicate venue reservation {key}")
        if amount_each <= 0 or not self.can_reserve_pair(amount_each):
            raise RuntimeError(
                f"venue reservation denied key={key} amount_each={amount_each} "
                f"gemini_free={self.free_for('gemini')} kalshi_free={self.free_for('kalshi')}"
            )
        self.reservations[key] = {"gemini": amount_each, "kalshi": amount_each}

    def release(self, key: str) -> None:
        self.reservations.pop(key, None)

    def convert_to_deployed(self, key: str, actual: dict[str, D]) -> None:
        reserved = self.reservations.pop(key, None)
        if reserved is None:
            raise RuntimeError(f"missing venue reservation {key}")
        for venue in ("gemini", "kalshi"):
            value = actual[venue]
            if value < 0 or value > reserved[venue]:
                raise RuntimeError(
                    f"actual {venue} capital exceeds reservation key={key} actual={value} reserved={reserved[venue]}"
                )
            self.unsettled[venue] = self.unsettled.get(venue, D("0")) + value

    def settle(self, actual: dict[str, D]) -> None:
        for venue in ("gemini", "kalshi"):
            value = actual[venue]
            if value < 0 or value > self.unsettled.get(venue, D("0")):
                raise RuntimeError(f"invalid {venue} settlement release actual={value} unsettled={self.unsettled.get(venue)}")
            self.unsettled[venue] -= value


class ParallelState:
    def __init__(self, campaign_id: str, hard_budget: D, venue_balances: dict[str, D]) -> None:
        self.campaign_id = campaign_id
        self.capital = CapitalBook(hard_budget)
        self.venues = VenueCapitalBook(venue_balances)
        self.lanes = {asset: Lane(asset) for asset in ASSETS}
        self.completed_total = 0
        self.execution_attempts_total = 0
        self.halted = False
        self.halt_reason: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "completed_total": self.completed_total,
            "execution_attempts_total": self.execution_attempts_total,
            "hard_budget_usd": str(self.capital.hard_budget),
            "unsettled_deployed_usd": str(self.capital.unsettled_deployed),
            "reserved_capital_usd": str(self.capital.reserved),
            "available_capital_usd": str(self.capital.available),
            "venue_balances_usd": {v: str(x) for v, x in self.venues.available_balances.items()},
            "venue_reserved_usd": {v: str(self.venues.reserved_for(v)) for v in ("gemini", "kalshi")},
            "venue_unsettled_usd": {v: str(self.venues.unsettled[v]) for v in ("gemini", "kalshi")},
            "venue_free_usd": {v: str(self.venues.free_for(v)) for v in ("gemini", "kalshi")},
            "reservations": {k: str(v) for k, v in self.capital.reservations.items()},
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "lanes": {a: {"status": lane.status, "execution_attempts": lane.execution_attempts, "completed_trades": lane.completed_trades, "active_run_id": lane.active_run_id, "reserved_usd": str(lane.reserved_usd)} for a, lane in self.lanes.items()},
        }


def _write_state(state: ParallelState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{state.campaign_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.snapshot(), separators=(",", ":"), sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _run_id(campaign_id: str, asset: str, n: int) -> str:
    return f"{campaign_id}-{asset.lower()}-{n:02d}"


def _asset_child_env(run_id: str, asset: str) -> dict[str, str]:
    asset = asset.strip().upper()
    if asset not in ASSETS:
        raise ValueError(f"unsupported asset lane {asset}")
    env = os.environ.copy()
    env[v2.RUN_ID_ENV] = run_id
    env[v2.ARM_ENV] = "YES"
    env["CRYPTO_CANARY_ASSET"] = asset
    return env


def _venue_costs(result: dict[str, Any]) -> dict[str, D]:
    out: dict[str, D] = {}
    for venue in ("gemini", "kalshi"):
        row = result.get(venue) or {}
        qty = D(str(row.get("filled_quantity") or "0"))
        avg = D(str(row.get("average_fill_price") or "0"))
        fee = D(str(row.get("fee_paid") or "0"))
        if qty <= 0:
            raise RuntimeError(f"missing filled quantity for {venue}")
        out[venue] = qty * avg + fee
    return out


async def _live_venue_balances() -> dict[str, D]:
    gemini_available = await _gemini_available_usd()
    kalshi = KalshiV2Executor()
    try:
        response = await kalshi.client.get("/portfolio/balance")
        response.raise_for_status()
        kalshi_available = D(str(response.json().get("balance") or "0")) / D("100")
    finally:
        await kalshi.close()
    return {"gemini": gemini_available, "kalshi": kalshi_available}


async def _obtain_asset_execution(run_id: str, asset: str, arm_deadline: datetime) -> dict[str, Any]:
    arm, claim, result_path = v2._paths(run_id)
    if result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8"))
    if claim.is_file():
        raise RuntimeError(f"attempt claim exists without result for {run_id}")
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "src.gemini_kalshi_crypto_campaign_child_reliable", env=_asset_child_env(run_id, asset))
    wait_task = asyncio.create_task(process.wait())
    try:
        while base._utcnow() <= arm_deadline:
            if result_path.is_file():
                return json.loads(result_path.read_text(encoding="utf-8"))
            if wait_task.done():
                raise RuntimeError(f"asset child exited {wait_task.result()} before result run_id={run_id}")
            await asyncio.sleep(base.CHILD_RESULT_POLL_S)
        raise TimeoutError(f"campaign arm deadline reached before execution for {run_id}")
    finally:
        await base._stop_child(process)
        if not wait_task.done():
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
        if arm.is_file() and not claim.is_file():
            try:
                arm.unlink()
            except FileNotFoundError:
                pass


async def _lane_loop(state: ParallelState, asset: str, arm_deadline: datetime, http: httpx.AsyncClient, lock: asyncio.Lock) -> None:
    lane = state.lanes[asset]
    while base._utcnow() <= arm_deadline:
        async with lock:
            if state.halted or state.completed_total >= MAX_COMPLETED_TRADES or state.execution_attempts_total >= MAX_EXECUTION_ATTEMPTS:
                return
            enough_global = state.capital.available >= RESERVE_PER_ATTEMPT
            enough_venues = state.venues.can_reserve_pair(VENUE_RESERVE_PER_ATTEMPT)
            if not enough_global or not enough_venues:
                lane.status = "WAITING_CAPITAL"
                _write_state(state)
                should_wait = True
            else:
                should_wait = False
                lane.execution_attempts += 1
                state.execution_attempts_total += 1
                run_id = _run_id(state.campaign_id, asset, lane.execution_attempts)
                reservation_key = run_id
                state.capital.reserve(reservation_key, RESERVE_PER_ATTEMPT)
                try:
                    state.venues.reserve_pair(reservation_key, VENUE_RESERVE_PER_ATTEMPT)
                except Exception:
                    state.capital.release(reservation_key)
                    raise
                lane.reserved_usd = RESERVE_PER_ATTEMPT
                lane.active_run_id = run_id
                lane.status = "WAITING_EXECUTION"
                _write_state(state)
                logger.warning(
                    "CRYPTO_PARALLEL_LANE_ARMED asset=%s run_id=%s pair_reserved=%s venue_reserved_each=%s "
                    "global_available=%s gemini_free=%s kalshi_free=%s",
                    asset, run_id, RESERVE_PER_ATTEMPT, VENUE_RESERVE_PER_ATTEMPT,
                    state.capital.available, state.venues.free_for("gemini"), state.venues.free_for("kalshi"),
                )
        if should_wait:
            await asyncio.sleep(0.25)
            continue
        actual_venue: dict[str, D] | None = None
        try:
            record = await _obtain_asset_execution(run_id, asset, arm_deadline)
            result = record.get("result") or {}
            if reliable._benign_primary_miss(result):
                async with lock:
                    state.capital.release(reservation_key)
                    state.venues.release(reservation_key)
                    lane.reserved_usd = D("0")
                    lane.active_run_id = None
                    lane.status = "IDLE"
                    _write_state(state)
                logger.warning("CRYPTO_PARALLEL_BENIGN_NO_FILL asset=%s run_id=%s continuing=true", asset, run_id)
                continue
            if str(result.get("outcome") or "") != "BOTH_FILLED" or bool(result.get("hard_halt_required")):
                await base._emit_execution_events(http, run_id, record)
                raise RuntimeError(f"unsafe live execution asset={asset} outcome={result.get('outcome')}")
            costs = await base._emit_execution_events(http, run_id, record)
            actual_capital = D(str(costs["capital"]))
            actual_venue = _venue_costs(result)
            async with lock:
                state.capital.convert_to_deployed(reservation_key, actual_capital)
                state.venues.convert_to_deployed(reservation_key, actual_venue)
                lane.reserved_usd = D("0")
                lane.status = "WAITING_SETTLEMENT"
                _write_state(state)
            gp = record.get("gemini_payload") or {}
            kp = record.get("kalshi_payload") or {}
            settlement = await base._official_settlement(http, gemini_symbol=str(gp.get("symbol") or ""), kalshi_ticker=str(kp.get("ticker") or ""))
            net_pnl = await base._emit_realized_event(http, run_id=run_id, record=record, costs=costs, settlement=settlement)
            async with lock:
                state.capital.settle(actual_capital)
                state.venues.settle(actual_venue)
                lane.completed_trades += 1
                state.completed_total += 1
                lane.active_run_id = None
                lane.status = "IDLE"
                _write_state(state)
            logger.warning("CRYPTO_PARALLEL_REALIZED asset=%s run_id=%s net_pnl=%s capital=%s completed_total=%s", asset, run_id, net_pnl, actual_capital, state.completed_total)
            if not bool(settlement["venue_outcomes_agree"]):
                raise RuntimeError(f"settlement disagreement asset={asset} run_id={run_id}")
        except Exception as exc:
            async with lock:
                if reservation_key in state.capital.reservations:
                    state.capital.release(reservation_key)
                if reservation_key in state.venues.reservations:
                    state.venues.release(reservation_key)
                lane.reserved_usd = D("0")
                lane.status = "HALTED"
                state.halted = True
                state.halt_reason = f"{asset}:{type(exc).__name__}:{str(exc)[:300]}"
                _write_state(state)
            logger.exception("CRYPTO_PARALLEL_HALT asset=%s run_id=%s", asset, run_id)
            return


async def run() -> None:
    campaign_id = base._clean_campaign_id(os.getenv(base.CAMPAIGN_ID_ENV, ""))
    deadline_raw = os.getenv("SOL_CANARY_V2_ARM_NOT_AFTER_UTC", "").strip()
    if not deadline_raw:
        raise RuntimeError("SOL_CANARY_V2_ARM_NOT_AFTER_UTC is required")
    arm_deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    if base._utcnow() > arm_deadline:
        raise RuntimeError("campaign arm deadline already expired")
    if os.getenv(v2.ARM_ENV, "NO").strip().upper() != "YES" or os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() != "YES":
        raise PermissionError("parallel campaign requires both explicit live gates")
    if not D("0") < MAX_TOTAL_RESERVED <= D("5") or not D("0") < RESERVE_PER_ATTEMPT <= D("1"):
        raise ValueError("invalid global capital reservation configuration")
    if not D("0") < VENUE_RESERVE_PER_ATTEMPT <= D("1"):
        raise ValueError("invalid per-venue reservation configuration")
    if not 1 <= MAX_COMPLETED_TRADES <= 20 or not MAX_COMPLETED_TRADES <= MAX_EXECUTION_ATTEMPTS <= 100:
        raise ValueError("invalid campaign attempt limits")
    venue_balances = await _live_venue_balances()
    if venue_balances["gemini"] < VENUE_RESERVE_PER_ATTEMPT or venue_balances["kalshi"] < VENUE_RESERVE_PER_ATTEMPT:
        raise RuntimeError(f"insufficient venue balances for one reserved pair: {venue_balances}")
    state = ParallelState(campaign_id, MAX_TOTAL_RESERVED, venue_balances)
    lock = asyncio.Lock()
    _write_state(state)
    logger.warning(
        "CRYPTO_PARALLEL_CAMPAIGN_ENABLED campaign_id=%s assets=%s one_unresolved_per_asset=true "
        "shared_capital_reservations=true per_venue_reservations=true hard_budget=%s reserve_per_attempt=%s "
        "venue_reserve_each=%s gemini_available=%s kalshi_available=%s min_profit=0.03 "
        "min_double_boundary_bps=10 execution_delay_ms=50 risk_gates_changed=false",
        campaign_id, ",".join(ASSETS), MAX_TOTAL_RESERVED, RESERVE_PER_ATTEMPT,
        VENUE_RESERVE_PER_ATTEMPT, venue_balances["gemini"], venue_balances["kalshi"],
    )
    async with httpx.AsyncClient(timeout=20.0) as http:
        await asyncio.gather(*[_lane_loop(state, asset, arm_deadline, http, lock) for asset in ASSETS])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()

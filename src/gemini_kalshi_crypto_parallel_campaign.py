"""Shared primitives for safe parallel-per-asset crypto execution.

Provides one-lane-per-asset state, a global capital-at-risk reservation ledger, and
per-venue USD reservations so concurrent BTC/ETH/SOL/XRP lanes cannot spend the same
Gemini or Kalshi dollars twice. This module does not start a campaign by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any

from src import gemini_kalshi_sol_live_canary_v2 as v2
from src.gemini_kalshi_live_service import _gemini_available_usd
from src.gemini_kalshi_probe_transport import KalshiV2Executor

D = Decimal
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
            "lanes": {
                a: {
                    "status": lane.status,
                    "execution_attempts": lane.execution_attempts,
                    "completed_trades": lane.completed_trades,
                    "active_run_id": lane.active_run_id,
                    "reserved_usd": str(lane.reserved_usd),
                }
                for a, lane in self.lanes.items()
            },
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

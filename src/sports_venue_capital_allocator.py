"""Low-latency per-venue capital guard for automated sports pairs.

A live arb must be fundable on *both* venues before either leg can be submitted.
Doing a REST balance request after a 50ms quote trigger would destroy the opportunity,
so this module maintains a separately refreshed balance snapshot and makes the hot
path decision entirely in memory.

This is deliberately separate from the cumulative live-probe budget guard:
- cumulative budget answers "are we still authorized to risk this much overall?"
- this allocator answers "can Gemini AND Kalshi independently fund these exact legs?"

Fail closed:
- stale/missing balance snapshots block submission;
- exact payload contract cost plus conservative modeled taker fee is reserved;
- a configurable free-cash buffer remains unused at each venue;
- every authorized route is copied into the durable trade context before reservation;
- no order-entry methods live in this module.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import logging
import os
import time
from typing import Any

from src.auth import KalshiClient
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.gemini_prediction_auth import private_rest_request

D = Decimal
logger = logging.getLogger("kalshi_arb.sports_venue_capital_allocator")
KALSHI_PROD_REST = "https://external-api.kalshi.com/trade-api/v2"

DEFAULT_MAX_SNAPSHOT_AGE_MS = int(os.getenv("SPORTS_CAPITAL_MAX_SNAPSHOT_AGE_MS", "5000"))
DEFAULT_FREE_BUFFER = D(os.getenv("SPORTS_VENUE_FREE_BUFFER_USD", "5"))
DEFAULT_LOG_INTERVAL_SECONDS = float(os.getenv("SPORTS_CAPITAL_LOG_INTERVAL_SECONDS", "60"))


class VenueCapitalUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RouteCapitalRequirement:
    gemini_required: Decimal
    kalshi_required: Decimal
    total_required: Decimal
    gemini_contract_cost: Decimal
    kalshi_contract_cost: Decimal
    gemini_fee: Decimal
    kalshi_fee: Decimal

    def as_dict(self) -> dict[str, str]:
        return {
            "gemini_required": str(self.gemini_required),
            "kalshi_required": str(self.kalshi_required),
            "total_required": str(self.total_required),
            "gemini_contract_cost": str(self.gemini_contract_cost),
            "kalshi_contract_cost": str(self.kalshi_contract_cost),
            "gemini_fee": str(self.gemini_fee),
            "kalshi_fee": str(self.kalshi_fee),
        }


@dataclass(frozen=True)
class VenueCapitalSnapshot:
    gemini_available: Decimal
    kalshi_available: Decimal
    observed_ms: int

    def age_ms(self, now_ms: int | None = None) -> int:
        now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        return max(0, now_ms - int(self.observed_ms))

    def as_dict(self, *, now_ms: int | None = None) -> dict[str, str | int]:
        return {
            "gemini_available": str(self.gemini_available),
            "kalshi_available": str(self.kalshi_available),
            "observed_ms": self.observed_ms,
            "age_ms": self.age_ms(now_ms),
        }


def route_requirement(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    kalshi_fee_multiplier: Decimal,
) -> RouteCapitalRequirement:
    quantity = D(str(gemini_payload.get("quantity") or "0"))
    k_quantity = D(str(kalshi_payload.get("count") or "0"))
    if quantity <= 0 or quantity != k_quantity:
        raise ValueError("capital allocator requires equal positive quantities")
    if str(gemini_payload.get("side") or "") != "BUY":
        raise ValueError("Gemini capital requirement supports BUY outcome legs only")

    gemini_price = D(str(gemini_payload.get("price")))
    kalshi_yes_price = D(str(kalshi_payload.get("price")))
    k_side = str(kalshi_payload.get("side") or "")
    if not D("0") < gemini_price < D("1") or not D("0") < kalshi_yes_price < D("1"):
        raise ValueError("prediction price outside (0,1)")
    if k_side == "bid":
        kalshi_contract_price = kalshi_yes_price
    elif k_side == "ask":
        kalshi_contract_price = D("1") - kalshi_yes_price
    else:
        raise ValueError("unsupported Kalshi side")

    gemini_contract = gemini_price * quantity
    kalshi_contract = kalshi_contract_price * quantity
    gemini_fee = gemini_predictions_taker_fee(gemini_price, quantity)
    kalshi_fee = kalshi_quadratic_taker_fee(
        kalshi_yes_price,
        quantity,
        fee_multiplier=D(kalshi_fee_multiplier),
    )
    g_required = gemini_contract + gemini_fee
    k_required = kalshi_contract + kalshi_fee
    return RouteCapitalRequirement(
        gemini_required=g_required,
        kalshi_required=k_required,
        total_required=g_required + k_required,
        gemini_contract_cost=gemini_contract,
        kalshi_contract_cost=kalshi_contract,
        gemini_fee=gemini_fee,
        kalshi_fee=kalshi_fee,
    )


def assert_route_capacity(
    snapshot: VenueCapitalSnapshot | None,
    requirement: RouteCapitalRequirement,
    *,
    max_snapshot_age_ms: int = DEFAULT_MAX_SNAPSHOT_AGE_MS,
    free_buffer: Decimal = DEFAULT_FREE_BUFFER,
    now_ms: int | None = None,
) -> None:
    if snapshot is None:
        raise VenueCapitalUnavailable("venue balance snapshot missing")
    age = snapshot.age_ms(now_ms)
    if age > max_snapshot_age_ms:
        raise VenueCapitalUnavailable(
            f"venue balance snapshot stale: age_ms={age} max={max_snapshot_age_ms}"
        )
    buffer = max(D("0"), D(free_buffer))
    gemini_usable = max(D("0"), snapshot.gemini_available - buffer)
    kalshi_usable = max(D("0"), snapshot.kalshi_available - buffer)
    if gemini_usable < requirement.gemini_required:
        raise VenueCapitalUnavailable(
            "Gemini route capital unavailable: "
            f"usable={gemini_usable} required={requirement.gemini_required} buffer={buffer}"
        )
    if kalshi_usable < requirement.kalshi_required:
        raise VenueCapitalUnavailable(
            "Kalshi route capital unavailable: "
            f"usable={kalshi_usable} required={requirement.kalshi_required} buffer={buffer}"
        )


def route_audit_metadata(
    snapshot: VenueCapitalSnapshot,
    requirement: RouteCapitalRequirement,
    *,
    free_buffer: Decimal,
    max_snapshot_age_ms: int,
    now_ms: int | None = None,
) -> dict[str, Any]:
    buffer = max(D("0"), D(free_buffer))
    return {
        "venue_capital_requirement": requirement.as_dict(),
        "venue_capital_snapshot": snapshot.as_dict(now_ms=now_ms),
        "venue_capital_policy": {
            "free_buffer_usd_per_venue": str(buffer),
            "max_snapshot_age_ms": int(max_snapshot_age_ms),
            "gemini_usable_after_buffer": str(max(D("0"), snapshot.gemini_available - buffer)),
            "kalshi_usable_after_buffer": str(max(D("0"), snapshot.kalshi_available - buffer)),
        },
    }


async def _gemini_available_usd() -> Decimal:
    response = await private_rest_request("POST", "/v1/balances", timeout=5.0)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError("unexpected Gemini balances response")
    for row in rows:
        if str(row.get("currency") or "").upper() == "USD":
            return D(str(row.get("available") or "0"))
    return D("0")


class CachedVenueCapitalAllocator:
    """Background balance refresher with a synchronous in-memory hot-path check."""

    def __init__(
        self,
        *,
        refresh_seconds: float = 2.0,
        max_snapshot_age_ms: int = DEFAULT_MAX_SNAPSHOT_AGE_MS,
        free_buffer: Decimal = DEFAULT_FREE_BUFFER,
        log_interval_seconds: float = DEFAULT_LOG_INTERVAL_SECONDS,
        kalshi_client: KalshiClient | None = None,
    ) -> None:
        self.refresh_seconds = max(0.5, float(refresh_seconds))
        self.max_snapshot_age_ms = max(500, int(max_snapshot_age_ms))
        self.free_buffer = max(D("0"), D(free_buffer))
        self.log_interval_seconds = max(5.0, float(log_interval_seconds))
        self.kalshi = kalshi_client or KalshiClient(base_url=KALSHI_PROD_REST)
        self._owns_kalshi = kalshi_client is None
        self.snapshot: VenueCapitalSnapshot | None = None
        self.last_error: str | None = None
        self._last_logged_balances: tuple[Decimal, Decimal] | None = None
        self._last_log_monotonic = 0.0

    async def close(self) -> None:
        if self._owns_kalshi:
            await self.kalshi.close()

    async def refresh_once(self) -> VenueCapitalSnapshot:
        async def kalshi_balance() -> Decimal:
            response = await self.kalshi.get("/portfolio/balance")
            response.raise_for_status()
            return D(str(response.json().get("balance") or "0")) / D("100")

        gemini_available, kalshi_available = await asyncio.gather(
            _gemini_available_usd(), kalshi_balance()
        )
        snapshot = VenueCapitalSnapshot(
            gemini_available=gemini_available,
            kalshi_available=kalshi_available,
            observed_ms=int(time.time() * 1000),
        )
        self.snapshot = snapshot
        self.last_error = None
        return snapshot

    def _should_log_snapshot(self, snapshot: VenueCapitalSnapshot) -> bool:
        balances = (snapshot.gemini_available, snapshot.kalshi_available)
        now = time.monotonic()
        changed = balances != self._last_logged_balances
        periodic = now - self._last_log_monotonic >= self.log_interval_seconds
        if changed or periodic:
            self._last_logged_balances = balances
            self._last_log_monotonic = now
            return True
        return False

    async def run(self) -> None:
        while True:
            try:
                snapshot = await self.refresh_once()
                if self._should_log_snapshot(snapshot):
                    logger.info(
                        "SPORTS_VENUE_CAPITAL_SNAPSHOT gemini=%s kalshi=%s free_buffer=%s refresh_seconds=%s",
                        snapshot.gemini_available,
                        snapshot.kalshi_available,
                        self.free_buffer,
                        self.refresh_seconds,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Keep the old snapshot, which will naturally age out and fail closed.
                self.last_error = f"{type(exc).__name__}:{str(exc)[:160]}"
                logger.warning("SPORTS_VENUE_CAPITAL_REFRESH_ERROR %s", self.last_error)
            await asyncio.sleep(self.refresh_seconds)

    def authorize(
        self,
        gemini_payload: dict[str, Any],
        kalshi_payload: dict[str, Any],
        *,
        kalshi_fee_multiplier: Decimal,
    ) -> RouteCapitalRequirement:
        requirement = route_requirement(
            gemini_payload,
            kalshi_payload,
            kalshi_fee_multiplier=kalshi_fee_multiplier,
        )
        assert_route_capacity(
            self.snapshot,
            requirement,
            max_snapshot_age_ms=self.max_snapshot_age_ms,
            free_buffer=self.free_buffer,
        )
        return requirement


def install(
    canary_module: Any,
    allocator: CachedVenueCapitalAllocator,
    *,
    run_id_env: str | None = None,
) -> None:
    """Wrap an already-guarded submit function without adding network I/O to it."""
    inner = canary_module.submit_dual_probe

    async def capital_checked_submit(
        gemini: Any,
        kalshi: Any,
        gemini_payload: dict[str, Any],
        kalshi_payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        multiplier = D(str(kwargs.get("kalshi_fee_multiplier")))
        requirement = allocator.authorize(
            gemini_payload,
            kalshi_payload,
            kalshi_fee_multiplier=multiplier,
        )
        snapshot = allocator.snapshot
        assert snapshot is not None  # authorize() already fails closed if missing
        audit = route_audit_metadata(
            snapshot,
            requirement,
            free_buffer=allocator.free_buffer,
            max_snapshot_age_ms=allocator.max_snapshot_age_ms,
        )
        if run_id_env:
            # The canary's SUBMITTING log has already populated this context. Add
            # exact venue-capital proof before the inner durable reservation runs.
            from src import sports_trade_reporting as reporting
            run_id = os.getenv(run_id_env, "").strip()
            if not run_id:
                raise VenueCapitalUnavailable(f"{run_id_env} missing for capital audit context")
            reporting._CONTEXT.setdefault(run_id, {}).update(audit)
        logger.warning(
            "SPORTS_VENUE_CAPITAL_AUTHORIZED gemini_required=%s kalshi_required=%s total=%s snapshot_age_ms=%s",
            requirement.gemini_required,
            requirement.kalshi_required,
            requirement.total_required,
            snapshot.age_ms(),
        )
        return await inner(
            gemini,
            kalshi,
            gemini_payload,
            kalshi_payload,
            **kwargs,
        )

    canary_module.submit_dual_probe = capital_checked_submit

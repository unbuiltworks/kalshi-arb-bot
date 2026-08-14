"""Hard-gated cross-exchange execution core.

This module contains the first live-capable primitives for Polymarket US <-> Kalshi
arbitrage, but it is deliberately impossible to use accidentally:

* live submission requires TWO explicit environment gates;
* only FULL_RULES_AUDITED_EXECUTABLE pairs are accepted;
* exposure is hard-capped at $10 in code;
* both venue orders use fill-or-kill semantics;
* a one-sided fill is treated as a critical legging incident and MUST invoke a
  recovery callback supplied by the caller.

There is intentionally no CLI/daemon entrypoint in this file. Importing it or
deploying the branch cannot place an order by itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import logging
import os
from typing import Any, Awaitable, Callable, Protocol

from src import polymarket_kalshi_shadow as calc

logger = logging.getLogger("kalshi_arb.cross_market_live")

EXECUTABLE_STATUS = "FULL_RULES_AUDITED_EXECUTABLE"
HARD_MAX_TOTAL_COST = Decimal("10.00")
LIVE_CONFIRMATION = "I_ACCEPT_LEGGING_RISK"


@dataclass(frozen=True)
class ExecutablePair:
    pair_id: str
    poly_slug: str
    kalshi_ticker: str
    settlement_status: str


@dataclass(frozen=True)
class LiveGate:
    enabled: bool
    confirmation: str
    max_total_cost: Decimal
    min_profit: Decimal

    @classmethod
    def from_env(cls) -> "LiveGate":
        enabled = os.getenv("CROSS_MARKET_LIVE_ENABLED", "").strip().lower() == "true"
        confirmation = os.getenv("CROSS_MARKET_LIVE_CONFIRMATION", "").strip()
        requested_cap = Decimal(os.getenv("CROSS_MARKET_MAX_TOTAL_COST", "10.00"))
        min_profit = Decimal(os.getenv("CROSS_MARKET_MIN_PROFIT", "0.05"))
        return cls(
            enabled=enabled,
            confirmation=confirmation,
            max_total_cost=min(requested_cap, HARD_MAX_TOTAL_COST),
            min_profit=max(Decimal("0"), min_profit),
        )

    def assert_armed(self) -> None:
        if not self.enabled:
            raise RuntimeError("Cross-market live execution is disabled")
        if self.confirmation != LIVE_CONFIRMATION:
            raise RuntimeError("Cross-market live confirmation phrase is missing")
        if self.max_total_cost <= 0 or self.max_total_cost > HARD_MAX_TOTAL_COST:
            raise RuntimeError("Invalid cross-market exposure cap")


@dataclass(frozen=True)
class VenueFill:
    venue: str
    requested: int
    filled: int
    average_price: Decimal | None = None
    fee: Decimal | None = None
    order_id: str | None = None
    raw: Any = None

    @property
    def full(self) -> bool:
        return self.filled == self.requested

    @property
    def empty(self) -> bool:
        return self.filled == 0


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str
    pair_id: str
    poly: VenueFill
    kalshi: VenueFill
    recovery_attempted: bool = False


class VenueAdapter(Protocol):
    async def preview(self, *, market: str, side: str, price: Decimal, quantity: int) -> Any: ...
    async def submit_fok(self, *, market: str, side: str, price: Decimal, quantity: int) -> VenueFill: ...


RecoveryCallback = Callable[
    [ExecutablePair, calc.ShadowPlan, VenueFill, VenueFill], Awaitable[None]
]


class LiveCrossMarketExecutor:
    """Execute two FOK legs concurrently behind explicit safety gates."""

    def __init__(
        self,
        *,
        poly: VenueAdapter,
        kalshi: VenueAdapter,
        recovery: RecoveryCallback,
        gate: LiveGate | None = None,
    ) -> None:
        self.poly = poly
        self.kalshi = kalshi
        self.recovery = recovery
        self.gate = gate or LiveGate.from_env()
        self._lock = asyncio.Lock()

    def _validate(self, pair: ExecutablePair, plan: calc.ShadowPlan) -> None:
        self.gate.assert_armed()
        if pair.settlement_status != EXECUTABLE_STATUS:
            raise RuntimeError(
                f"Pair {pair.pair_id} is not settlement-certified executable: "
                f"{pair.settlement_status}"
            )
        if plan.quantity <= 0:
            raise RuntimeError("Quantity must be positive")
        if plan.profit < self.gate.min_profit:
            raise RuntimeError(
                f"Profit {plan.profit} is below live minimum {self.gate.min_profit}"
            )
        if plan.total_cost > self.gate.max_total_cost:
            raise RuntimeError(
                f"Trade cost {plan.total_cost} exceeds live cap {self.gate.max_total_cost}"
            )
        if plan.total_cost > HARD_MAX_TOTAL_COST:
            raise RuntimeError("Trade exceeds hard-coded $10 safety ceiling")

    async def execute(self, pair: ExecutablePair, plan: calc.ShadowPlan) -> ExecutionOutcome:
        """Preview, then concurrently submit both FOK legs.

        A recovery callback is mandatory at construction time. If exactly one leg
        fills, it is invoked immediately before this method returns.
        """
        async with self._lock:
            self._validate(pair, plan)

            # Pre-trade validation. A preview failure aborts before either venue is touched.
            await asyncio.gather(
                self.poly.preview(
                    market=pair.poly_slug,
                    side=plan.poly_side,
                    price=plan.poly_price,
                    quantity=plan.quantity,
                ),
                self.kalshi.preview(
                    market=pair.kalshi_ticker,
                    side=plan.kalshi_side,
                    price=plan.kalshi_price,
                    quantity=plan.quantity,
                ),
            )

            logger.warning(
                "CROSS_MARKET_LIVE_SUBMIT pair=%s qty=%d cost=%s modeled_profit=%s",
                pair.pair_id,
                plan.quantity,
                plan.total_cost,
                plan.profit,
            )

            poly_fill, kalshi_fill = await asyncio.gather(
                self.poly.submit_fok(
                    market=pair.poly_slug,
                    side=plan.poly_side,
                    price=plan.poly_price,
                    quantity=plan.quantity,
                ),
                self.kalshi.submit_fok(
                    market=pair.kalshi_ticker,
                    side=plan.kalshi_side,
                    price=plan.kalshi_price,
                    quantity=plan.quantity,
                ),
            )

            if poly_fill.full and kalshi_fill.full:
                return ExecutionOutcome("BOTH_FILLED", pair.pair_id, poly_fill, kalshi_fill)
            if poly_fill.empty and kalshi_fill.empty:
                return ExecutionOutcome("NEITHER_FILLED", pair.pair_id, poly_fill, kalshi_fill)

            # FOK should prevent partial fills. Treat any non-{0, requested} result as critical too.
            logger.critical(
                "CROSS_MARKET_LEGGING pair=%s poly=%d/%d kalshi=%d/%d",
                pair.pair_id,
                poly_fill.filled,
                poly_fill.requested,
                kalshi_fill.filled,
                kalshi_fill.requested,
            )
            await self.recovery(pair, plan, poly_fill, kalshi_fill)
            return ExecutionOutcome(
                "LEGGING_RECOVERY_TRIGGERED",
                pair.pair_id,
                poly_fill,
                kalshi_fill,
                recovery_attempted=True,
            )

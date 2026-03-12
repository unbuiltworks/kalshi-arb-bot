"""
Risk layer — pre-trade filters and circuit breakers.

Wraps around the order manager to enforce:
  - Per-market and total position limits
  - Daily loss limit (halt bot)
  - Cooldown after legging incidents
  - Max concurrent arbs
  - Legging incident counter (halt after N failures)

The RiskManager sits between the spread scanner and order manager:
    scanner.on_signal → risk.check_and_forward → order_manager.handle_signal
"""

import asyncio
import logging
import time

from config.settings import (
    COOLDOWN_AFTER_LEG_FAIL_MS,
    MAX_CONCURRENT_ARBS,
    MAX_DAILY_LOSS_CENTS,
    MAX_LEG_FAILS_BEFORE_HALT,
    MAX_POSITION_PER_MARKET,
    MAX_TOTAL_POSITION,
)
from src.order_manager import ArbTrade, OrderManager
from src.spread_scanner import ArbSignal

logger = logging.getLogger("kalshi_arb.risk")


class RiskManager:
    """
    Pre-trade risk checks and circuit breakers.

    Usage:
        risk = RiskManager(order_manager)
        scanner.on_signal(risk.check_and_forward)
        order_manager.on_leg_fail(risk.handle_leg_fail)
    """

    def __init__(
        self,
        order_manager: OrderManager,
        max_position_per_market: int = MAX_POSITION_PER_MARKET,
        max_total_position: int = MAX_TOTAL_POSITION,
        max_concurrent_arbs: int = MAX_CONCURRENT_ARBS,
        max_daily_loss_cents: int = MAX_DAILY_LOSS_CENTS,
        max_leg_fails: int = MAX_LEG_FAILS_BEFORE_HALT,
        cooldown_ms: int = COOLDOWN_AFTER_LEG_FAIL_MS,
    ):
        self._om = order_manager
        self._max_pos_market = max_position_per_market
        self._max_pos_total = max_total_position
        self._max_concurrent = max_concurrent_arbs
        self._max_daily_loss = max_daily_loss_cents
        self._max_leg_fails = max_leg_fails
        self._cooldown_ms = cooldown_ms

        # State
        self._positions: dict[str, int] = {}  # market_ticker → net contracts held
        self._daily_loss_cents: int = 0
        self._leg_fail_count: int = 0
        self._cooldown_until_ms: int = 0
        self._halted: bool = False
        self._halt_reason: str = ""

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    @property
    def total_position(self) -> int:
        return sum(self._positions.values())

    def position_for(self, market_ticker: str) -> int:
        return self._positions.get(market_ticker, 0)

    @property
    def daily_loss_cents(self) -> int:
        return self._daily_loss_cents

    @property
    def leg_fail_count(self) -> int:
        return self._leg_fail_count

    @property
    def in_cooldown(self) -> bool:
        return int(time.time() * 1000) < self._cooldown_until_ms

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    async def check_and_forward(self, signal: ArbSignal) -> None:
        """
        Run all pre-trade checks, then forward to the order manager.

        This is the callback wired into scanner.on_signal().
        """
        reason = self._reject_reason(signal)
        if reason:
            logger.debug(
                "RISK REJECT %s: %s",
                signal.market_ticker, reason,
            )
            return

        await self._om.handle_signal(signal)

    def _reject_reason(self, signal: ArbSignal) -> str | None:
        """Return a rejection reason, or None if the trade is allowed."""
        if self._halted:
            return f"bot halted: {self._halt_reason}"

        if self.in_cooldown:
            remaining = self._cooldown_until_ms - int(time.time() * 1000)
            return f"cooldown active ({remaining}ms remaining)"

        if self._om.has_active_trade:
            active_count = sum(1 for t in self._om._active.values() if not t.completed)
            if active_count >= self._max_concurrent:
                return f"max concurrent arbs ({self._max_concurrent})"

        # Position limits
        market_pos = self._positions.get(signal.market_ticker, 0)
        new_market_pos = market_pos + signal.quantity
        if new_market_pos > self._max_pos_market:
            return (
                f"market position limit: {market_pos}+{signal.quantity}"
                f" > {self._max_pos_market}"
            )

        new_total = self.total_position + signal.quantity
        if new_total > self._max_pos_total:
            return (
                f"total position limit: {self.total_position}+{signal.quantity}"
                f" > {self._max_pos_total}"
            )

        # Daily loss
        if self._daily_loss_cents >= self._max_daily_loss:
            return f"daily loss limit reached: {self._daily_loss_cents}c >= {self._max_daily_loss}c"

        return None

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def record_fill(self, market_ticker: str, quantity: int) -> None:
        """Record a successful arb fill (both legs). Adds to position count."""
        self._positions[market_ticker] = self._positions.get(market_ticker, 0) + quantity
        logger.info(
            "Position update %s: %d (total: %d)",
            market_ticker, self._positions[market_ticker], self.total_position,
        )

    def record_settlement(self, market_ticker: str, quantity: int) -> None:
        """Record contracts settled/expired — reduces position."""
        current = self._positions.get(market_ticker, 0)
        self._positions[market_ticker] = max(0, current - quantity)
        if self._positions[market_ticker] == 0:
            self._positions.pop(market_ticker, None)

    def record_loss(self, loss_cents: int) -> None:
        """Record a realized loss (positive = loss). Checks halt condition."""
        self._daily_loss_cents += loss_cents
        logger.warning("Daily loss: %dc (limit: %dc)", self._daily_loss_cents, self._max_daily_loss)
        if self._daily_loss_cents >= self._max_daily_loss:
            self._halt("daily loss limit reached")

    # ------------------------------------------------------------------
    # Legging incident handler
    # ------------------------------------------------------------------

    async def handle_leg_fail(self, trade: ArbTrade) -> None:
        """
        Called when a legging incident occurs (maker filled, taker failed).

        Increments fail counter, activates cooldown, halts if threshold reached.
        """
        self._leg_fail_count += 1
        logger.warning(
            "Leg fail #%d/%d on %s",
            self._leg_fail_count,
            self._max_leg_fails,
            trade.signal.market_ticker,
        )

        # Activate cooldown
        self._cooldown_until_ms = int(time.time() * 1000) + self._cooldown_ms
        logger.info("Cooldown activated for %dms", self._cooldown_ms)

        # Halt if too many failures
        if self._leg_fail_count >= self._max_leg_fails:
            self._halt(
                f"too many legging incidents ({self._leg_fail_count}/{self._max_leg_fails})"
            )

    # ------------------------------------------------------------------
    # Halt / reset
    # ------------------------------------------------------------------

    def _halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason
        logger.critical("BOT HALTED: %s", reason)

    def reset_daily(self) -> None:
        """Reset daily counters (call at midnight or on manual reset)."""
        self._daily_loss_cents = 0
        logger.info("Daily loss counter reset")

    def resume(self) -> None:
        """Manual override to resume a halted bot."""
        self._halted = False
        self._halt_reason = ""
        self._leg_fail_count = 0
        self._cooldown_until_ms = 0
        logger.info("Bot resumed from halt")

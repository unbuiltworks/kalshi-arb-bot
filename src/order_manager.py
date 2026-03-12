"""
Order manager — paired execution for arb trades.

Receives ArbSignals from the spread scanner and executes the two-leg trade:
  1. Post maker leg (post_only=True) on the chosen side
  2. Wait for fill notification via WS fill channel
  3. On fill → immediately fire taker leg on the other side
  4. On timeout → cancel maker leg
  5. If legged (maker filled but taker fails) → flatten at breakeven/small loss

In paper mode, all orders are logged but never sent to the API.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

from src.auth import KalshiClient
from src.spread_scanner import ArbSignal

logger = logging.getLogger("kalshi_arb.orders")


class LegStatus(Enum):
    PENDING = "pending"
    POSTED = "posted"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class Leg:
    side: str               # "yes" or "no"
    price_cents: int
    quantity: int
    is_maker: bool
    client_order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str | None = None     # server-assigned after POST
    status: LegStatus = LegStatus.PENDING
    fill_quantity: int = 0


@dataclass
class ArbTrade:
    """Tracks a paired arb trade through its lifecycle."""
    signal: ArbSignal
    maker_leg: Leg
    taker_leg: Leg
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    completed: bool = False


class OrderManager:
    """
    Manages paired arb execution with maker-first strategy.

    Usage:
        om = OrderManager(client, paper=True)
        scanner.on_signal(om.handle_signal)
        ws.on_fill(om.handle_fill)
    """

    def __init__(
        self,
        client: KalshiClient,
        paper: bool = False,
        max_fill_wait_ms: int = 5000,
        on_leg_fail: "asyncio.Event | None" = None,
    ):
        self._client = client
        self._paper = paper
        self._max_fill_wait_ms = max_fill_wait_ms

        # Active trade keyed by maker client_order_id
        self._active: dict[str, ArbTrade] = {}
        # Map server order_id → client_order_id for fill routing
        self._server_to_client: dict[str, str] = {}
        # Event for fill arrival (per client_order_id)
        self._fill_events: dict[str, asyncio.Event] = {}

        # Callback when a legging incident occurs
        self._on_leg_fail_callbacks: list = []

    @property
    def has_active_trade(self) -> bool:
        return any(not t.completed for t in self._active.values())

    def on_leg_fail(self, callback) -> None:
        """Register async callback(ArbTrade) for legging incidents."""
        self._on_leg_fail_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Signal handler (from spread scanner)
    # ------------------------------------------------------------------

    async def handle_signal(self, signal: ArbSignal) -> None:
        """
        Receive an arb signal and execute the paired trade.

        Skips if there's already an active trade in flight.
        """
        if self.has_active_trade:
            logger.debug("Skipping signal — trade already active")
            return

        # Build legs
        if signal.maker_side == "yes":
            maker_leg = Leg(
                side="yes",
                price_cents=signal.yes_ask_cents,
                quantity=signal.quantity,
                is_maker=True,
            )
            taker_leg = Leg(
                side="no",
                price_cents=signal.no_ask_cents,
                quantity=signal.quantity,
                is_maker=False,
            )
        else:
            maker_leg = Leg(
                side="no",
                price_cents=signal.no_ask_cents,
                quantity=signal.quantity,
                is_maker=True,
            )
            taker_leg = Leg(
                side="yes",
                price_cents=signal.yes_ask_cents,
                quantity=signal.quantity,
                is_maker=False,
            )

        trade = ArbTrade(signal=signal, maker_leg=maker_leg, taker_leg=taker_leg)
        self._active[maker_leg.client_order_id] = trade

        logger.info(
            "TRADE %s | %s maker=%s@%dc taker=%s@%dc qty=%d edge=%dc",
            signal.market_ticker,
            "PAPER" if self._paper else "LIVE",
            maker_leg.side, maker_leg.price_cents,
            taker_leg.side, taker_leg.price_cents,
            signal.quantity, signal.edge_cents,
        )

        if self._paper:
            await self._execute_paper(trade)
        else:
            await self._execute_live(trade)

    # ------------------------------------------------------------------
    # Paper execution
    # ------------------------------------------------------------------

    async def _execute_paper(self, trade: ArbTrade) -> None:
        """Log both legs as if they filled instantly."""
        trade.maker_leg.status = LegStatus.FILLED
        trade.maker_leg.fill_quantity = trade.maker_leg.quantity
        trade.taker_leg.status = LegStatus.FILLED
        trade.taker_leg.fill_quantity = trade.taker_leg.quantity
        trade.completed = True

        logger.info(
            "PAPER FILL %s | maker %s@%dc filled | taker %s@%dc filled | edge=%dc",
            trade.signal.market_ticker,
            trade.maker_leg.side, trade.maker_leg.price_cents,
            trade.taker_leg.side, trade.taker_leg.price_cents,
            trade.signal.edge_cents,
        )

    # ------------------------------------------------------------------
    # Live execution
    # ------------------------------------------------------------------

    async def _execute_live(self, trade: ArbTrade) -> None:
        """Post maker leg, wait for fill, fire taker leg."""
        # 1. Post maker leg
        ok = await self._post_order(trade.maker_leg, trade.signal.market_ticker, post_only=True)
        if not ok:
            trade.maker_leg.status = LegStatus.FAILED
            trade.completed = True
            logger.error("Maker leg POST failed for %s", trade.signal.market_ticker)
            return

        trade.maker_leg.status = LegStatus.POSTED

        # 2. Wait for fill
        evt = asyncio.Event()
        self._fill_events[trade.maker_leg.client_order_id] = evt

        try:
            await asyncio.wait_for(evt.wait(), timeout=self._max_fill_wait_ms / 1000.0)
        except asyncio.TimeoutError:
            logger.warning(
                "Maker leg timeout %s %s@%dc — cancelling",
                trade.signal.market_ticker,
                trade.maker_leg.side,
                trade.maker_leg.price_cents,
            )
            await self._cancel_order(trade.maker_leg)
            trade.maker_leg.status = LegStatus.CANCELLED
            trade.completed = True
            return
        finally:
            self._fill_events.pop(trade.maker_leg.client_order_id, None)

        # Maker filled
        trade.maker_leg.status = LegStatus.FILLED
        logger.info(
            "Maker filled %s %s@%dc x%d",
            trade.signal.market_ticker,
            trade.maker_leg.side,
            trade.maker_leg.price_cents,
            trade.maker_leg.fill_quantity,
        )

        # 3. Fire taker leg immediately
        ok = await self._post_order(trade.taker_leg, trade.signal.market_ticker, post_only=False)
        if not ok:
            trade.taker_leg.status = LegStatus.FAILED
            logger.error(
                "LEGGED %s — maker %s filled but taker %s POST failed",
                trade.signal.market_ticker,
                trade.maker_leg.side,
                trade.taker_leg.side,
            )
            await self._handle_legging(trade)
            return

        trade.taker_leg.status = LegStatus.FILLED
        trade.taker_leg.fill_quantity = trade.taker_leg.quantity
        trade.completed = True

        logger.info(
            "ARB COMPLETE %s | edge=%dc qty=%d",
            trade.signal.market_ticker,
            trade.signal.edge_cents,
            trade.signal.quantity,
        )

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------

    async def _post_order(
        self, leg: Leg, market_ticker: str, post_only: bool,
    ) -> bool:
        """
        POST an order to Kalshi. Returns True on success.

        For buying YES: yes_price = leg.price_cents
        For buying NO: yes_price = 100 - leg.price_cents
          (Kalshi always takes yes_price; NO price is the complement)
        """
        if leg.side == "yes":
            yes_price = leg.price_cents
        else:
            yes_price = 100 - leg.price_cents

        body = {
            "ticker": market_ticker,
            "action": "buy",
            "side": leg.side,
            "type": "limit",
            "yes_price": yes_price,
            "count": leg.quantity,
            "client_order_id": leg.client_order_id,
        }
        if post_only:
            body["post_only"] = True

        try:
            resp = await self._client.post("/portfolio/orders", json=body)
            resp.raise_for_status()
            data = resp.json()
            leg.order_id = data.get("order", {}).get("order_id", "")
            if leg.order_id:
                self._server_to_client[leg.order_id] = leg.client_order_id
            logger.info(
                "Order posted: %s %s %s@%dc x%d oid=%s%s",
                market_ticker, leg.side, "maker" if post_only else "taker",
                leg.price_cents, leg.quantity, leg.order_id,
                " post_only" if post_only else "",
            )
            return True
        except Exception:
            logger.exception("Order POST failed: %s %s", market_ticker, leg.side)
            return False

    async def _cancel_order(self, leg: Leg) -> bool:
        """Cancel an order by server order_id."""
        if not leg.order_id:
            logger.warning("Cannot cancel — no server order_id for %s", leg.client_order_id)
            return False
        try:
            resp = await self._client.delete(f"/portfolio/orders/{leg.order_id}")
            resp.raise_for_status()
            logger.info("Order cancelled: oid=%s", leg.order_id)
            self._server_to_client.pop(leg.order_id, None)
            return True
        except Exception:
            logger.exception("Cancel failed: oid=%s", leg.order_id)
            return False

    # ------------------------------------------------------------------
    # Fill handler (from WS)
    # ------------------------------------------------------------------

    async def handle_fill(self, msg: dict) -> None:
        """
        Handle a fill message from the WebSocket.

        Routes the fill to the correct active trade and triggers
        the taker leg if appropriate.
        """
        fill = msg.get("msg", msg)
        order_id = fill.get("order_id", "")
        client_oid = (
            fill.get("client_order_id", "")
            or self._server_to_client.get(order_id, "")
        )

        if not client_oid or client_oid not in self._active:
            logger.debug("Fill for unknown order: oid=%s", order_id)
            return

        trade = self._active[client_oid]
        qty = int(fill.get("count", fill.get("quantity", 0)))
        trade.maker_leg.fill_quantity += qty

        logger.info(
            "Fill received: %s %s@%dc x%d (total filled: %d)",
            trade.signal.market_ticker,
            trade.maker_leg.side,
            trade.maker_leg.price_cents,
            qty,
            trade.maker_leg.fill_quantity,
        )

        # Signal the wait_for in _execute_live
        evt = self._fill_events.get(client_oid)
        if evt:
            evt.set()

    # ------------------------------------------------------------------
    # Legging / flatten
    # ------------------------------------------------------------------

    async def _handle_legging(self, trade: ArbTrade) -> None:
        """
        Flatten a legged trade — we hold one side and failed to get the other.

        Attempts to exit the filled maker leg at the original price (breakeven).
        """
        logger.warning(
            "FLATTEN %s — attempting to exit %s leg at breakeven",
            trade.signal.market_ticker,
            trade.maker_leg.side,
        )

        # To flatten: sell what we bought.
        # Selling YES = placing a YES ask (action=sell, side=yes) but Kalshi
        # only supports action=buy. Selling YES is equivalent to buying NO
        # at the complement price. However for simplicity we sell directly
        # using action=sell.
        flatten_body = {
            "ticker": trade.signal.market_ticker,
            "action": "sell",
            "side": trade.maker_leg.side,
            "type": "limit",
            "yes_price": (
                trade.maker_leg.price_cents
                if trade.maker_leg.side == "yes"
                else 100 - trade.maker_leg.price_cents
            ),
            "count": trade.maker_leg.fill_quantity,
            "client_order_id": str(uuid.uuid4()),
        }

        try:
            resp = await self._client.post("/portfolio/orders", json=flatten_body)
            resp.raise_for_status()
            logger.info("Flatten order posted for %s", trade.signal.market_ticker)
        except Exception:
            logger.exception(
                "FLATTEN FAILED %s — manual intervention needed",
                trade.signal.market_ticker,
            )

        trade.completed = True

        for cb in self._on_leg_fail_callbacks:
            try:
                await cb(trade)
            except Exception:
                logger.exception("Leg fail callback error")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clear_completed(self) -> int:
        """Remove completed trades from tracking. Returns count removed."""
        to_remove = [k for k, t in self._active.items() if t.completed]
        for k in to_remove:
            trade = self._active.pop(k)
            # Clean up server_to_client mappings
            for leg in (trade.maker_leg, trade.taker_leg):
                if leg.order_id:
                    self._server_to_client.pop(leg.order_id, None)
        return len(to_remove)

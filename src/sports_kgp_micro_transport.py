"""Immediate one-contract transports for the K/G/P sports micro-live canary.

This module is intentionally downstream of :mod:`sports_kgp_micro_guard`.  It does
not discover markets or decide settlement equivalence.  It accepts only a frozen
``MicroExecutionPlan`` whose rule fingerprint was approved elsewhere.

Live use is separately gated by ``ENABLE_SPORTS_KGP_MICRO_LIVE=YES`` and a one-use
arm-token file.  This module never creates the token.  The first canary is therefore
one automatic paired attempt, after which the process is disarmed.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal
import os
from pathlib import Path
from typing import Any, Mapping
import uuid

from polymarket_us import PolymarketUS

from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor
from src.sports_kgp_micro_guard import ALLOWED_VENUES, MicroExecutionPlan, MicroLeg

D = Decimal
ONE = D("1")
LIVE_ENV = "ENABLE_SPORTS_KGP_MICRO_LIVE"
DEFAULT_ARM_TOKEN = Path("/data/.sports_kgp_micro_arm_once")


@dataclass(frozen=True)
class VenueResult:
    venue: str
    order_id: str | None
    status: str
    filled_quantity: Decimal
    average_price: Decimal | None
    reason: str | None
    reconciled: bool = False

    @property
    def filled_one(self) -> bool:
        return self.filled_quantity == ONE

    @property
    def ambiguous(self) -> bool:
        return self.status.upper() == "UNKNOWN"

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["filled_quantity"] = str(self.filled_quantity)
        row["average_price"] = None if self.average_price is None else str(self.average_price)
        return row


def live_authorized(*, live_requested: bool, token_path: Path = DEFAULT_ARM_TOKEN) -> bool:
    return bool(live_requested) and os.getenv(LIVE_ENV, "NO").strip().upper() == "YES" and token_path.is_file()


def consume_arm_token(token_path: Path = DEFAULT_ARM_TOKEN) -> bool:
    """Consume authorization before the first mutation; never creates a token."""
    try:
        token_path.unlink()
        return True
    except FileNotFoundError:
        return False


def _yes_space_limit(leg: MicroLeg) -> Decimal:
    return leg.raw_outcome_price if leg.native_outcome == "YES" else ONE - leg.raw_outcome_price


def gemini_payload(leg: MicroLeg) -> dict[str, Any]:
    if leg.venue != "gemini":
        raise ValueError("not a Gemini leg")
    return {
        "symbol": leg.market_id,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "FOK",
        "price": str(leg.raw_outcome_price),
        "quantity": "1",
        "eventOutcome": leg.native_outcome,
        "clientOrderId": f"sports-kgp-{uuid.uuid4().hex[:20]}-g",
    }


def kalshi_payload(leg: MicroLeg) -> dict[str, Any]:
    if leg.venue != "kalshi":
        raise ValueError("not a Kalshi leg")
    return {
        "ticker": leg.market_id,
        "client_order_id": f"sports-kgp-{uuid.uuid4().hex[:20]}-k",
        "side": "bid" if leg.native_outcome == "YES" else "ask",
        "count": "1.00",
        "price": str(_yes_space_limit(leg)),
        "time_in_force": "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }


def polymarket_payload(leg: MicroLeg) -> dict[str, Any]:
    if leg.venue != "polymarket":
        raise ValueError("not a Polymarket leg")
    return {
        "marketSlug": leg.market_id,
        "intent": "ORDER_INTENT_BUY_LONG" if leg.native_outcome == "YES" else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(_yes_space_limit(leg)), "currency": "USD"},
        "quantity": 1,
        "tif": "TIME_IN_FORCE_FILL_OR_KILL",
        "participateDontInitiate": False,
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        "synchronousExecution": True,
        "maxBlockTime": "2",
    }


def payload_for(leg: MicroLeg) -> dict[str, Any]:
    if leg.venue == "gemini":
        return gemini_payload(leg)
    if leg.venue == "kalshi":
        return kalshi_payload(leg)
    if leg.venue == "polymarket":
        return polymarket_payload(leg)
    raise ValueError("automatic transport only supports K/G/P")


def validate_plan_transport(plan: MicroExecutionPlan) -> dict[str, dict[str, Any]]:
    """Freeze immediate payloads and independently enforce q=1/no-sportsbook shape."""
    if D(plan.quantity) != ONE:
        raise ValueError("micro transport is exactly one contract per leg")
    venues = {plan.yes_leg.venue, plan.no_leg.venue}
    if len(venues) != 2 or not venues.issubset(ALLOWED_VENUES):
        raise ValueError("micro transport accepts two distinct K/G/P venues only")
    if plan.yes_leg.normalized_outcome != "YES" or plan.no_leg.normalized_outcome != "NO":
        raise ValueError("transport legs are not complementary normalized outcomes")
    payloads = {
        plan.yes_leg.venue: payload_for(plan.yes_leg),
        plan.no_leg.venue: payload_for(plan.no_leg),
    }
    for venue, payload in payloads.items():
        if venue == "gemini":
            if payload.get("timeInForce") != "FOK" or payload.get("quantity") != "1":
                raise ValueError("Gemini micro leg is not q=1 FOK")
        elif venue == "kalshi":
            if payload.get("time_in_force") != "fill_or_kill" or D(str(payload.get("count"))) != ONE:
                raise ValueError("Kalshi micro leg is not q=1 FOK")
        elif venue == "polymarket":
            if payload.get("tif") != "TIME_IN_FORCE_FILL_OR_KILL" or int(payload.get("quantity", 0)) != 1:
                raise ValueError("Polymarket micro leg is not q=1 FOK")
            if payload.get("manualOrderIndicator") != "MANUAL_ORDER_INDICATOR_AUTOMATIC":
                raise ValueError("Polymarket automated-order indicator missing")
    return payloads


class PolymarketMicroExecutor:
    def __init__(self, client: PolymarketUS | None = None) -> None:
        self.client = client or PolymarketUS(
            key_id=os.environ["POLYMARKET_KEY_ID"],
            secret_key=os.environ["POLYMARKET_SECRET_KEY"],
            timeout=5.0,
        )
        self._owns = client is None

    def close(self) -> None:
        if self._owns:
            self.client.close()

    async def position(self, slug: str) -> Decimal:
        body = await asyncio.to_thread(self.client.portfolio.positions, {"market": slug, "limit": 10})
        positions = body.get("positions") or {}
        row = positions.get(slug) if isinstance(positions, Mapping) else None
        if row is None and isinstance(positions, list):
            row = next((x for x in positions if str(x.get("marketSlug") or x.get("market") or "") == slug), {})
        return D(str((row or {}).get("netPosition") or "0"))

    async def preflight(self, leg: MicroLeg) -> None:
        market = await asyncio.to_thread(self.client.markets.retrieve_by_slug, leg.market_id)
        min_qty = D(str(market.get("minimumTradeQty") or "1"))
        tick = D(str(market.get("orderPriceMinTickSize") or "0.01"))
        yes_limit = _yes_space_limit(leg)
        if min_qty > ONE or (ONE / min_qty) != (ONE / min_qty).to_integral_value():
            raise ValueError("Polymarket market does not accept exactly one contract")
        if tick <= 0 or (yes_limit / tick) != (yes_limit / tick).to_integral_value():
            raise ValueError("Polymarket price is not aligned to the current tick")
        if await self.position(leg.market_id) != 0:
            raise PermissionError("pre-existing Polymarket position blocks bounded recovery")

    @staticmethod
    def _response_fill(body: Mapping[str, Any]) -> tuple[Decimal, Decimal | None]:
        qty = D("0")
        avg: Decimal | None = None
        for execution in body.get("executions") or []:
            order = execution.get("order") or {}
            cumulative = D(str(order.get("cumQuantity") or "0"))
            if cumulative >= qty:
                qty = cumulative
                value = (order.get("avgPx") or {}).get("value")
                avg = D(str(value)) if value is not None and qty > 0 else avg
        return qty, avg

    async def place_fok(self, leg: MicroLeg) -> VenueResult:
        body_payload = polymarket_payload(leg)
        try:
            body = await asyncio.to_thread(self.client.orders.create, body_payload)
            order_id = str(body.get("id") or "") or None
            qty, avg = self._response_fill(body)
            if qty == ONE:
                return VenueResult("polymarket", order_id, "FILLED", ONE, avg, None)
            if order_id:
                try:
                    row = await asyncio.to_thread(self.client.orders.retrieve, order_id)
                    qty = D(str(row.get("cumQuantity") or "0"))
                    state = str(row.get("state") or "UNKNOWN").upper()
                    value = (row.get("avgPx") or {}).get("value")
                    avg = D(str(value)) if value is not None and qty > 0 else None
                    return VenueResult("polymarket", order_id, state, qty, avg, None, True)
                except Exception:
                    pass
            return VenueResult("polymarket", order_id, "CANCELED", D("0"), None, "FOK_NOT_FILLED")
        except Exception as exc:
            # A create timeout is potentially mutating. Reconcile from the known-zero
            # starting position instead of retrying the create.
            try:
                pos = await self.position(leg.market_id)
                expected = ONE if leg.native_outcome == "YES" else -ONE
                if pos == expected:
                    return VenueResult("polymarket", None, "FILLED", ONE, None, "RECONCILED_POSITION", True)
            except Exception:
                pass
            return VenueResult("polymarket", None, "UNKNOWN", D("0"), None, f"{type(exc).__name__}:{str(exc)[:160]}")

    async def emergency_flatten(self, leg: MicroLeg) -> VenueResult:
        try:
            await asyncio.to_thread(
                self.client.orders.close_position,
                {
                    "marketSlug": leg.market_id,
                    "slippageTolerance": {
                        "currentPrice": {"value": str(_yes_space_limit(leg)), "currency": "USD"},
                        "ticks": 20,
                    },
                },
            )
            for _ in range(20):
                if await self.position(leg.market_id) == 0:
                    return VenueResult("polymarket", None, "FLATTENED", ONE, None, None, True)
                await asyncio.sleep(0.1)
            return VenueResult("polymarket", None, "UNKNOWN", D("0"), None, "FLATTEN_POSITION_NOT_ZERO")
        except Exception as exc:
            return VenueResult("polymarket", None, "UNKNOWN", D("0"), None, f"FLATTEN_{type(exc).__name__}:{str(exc)[:140]}")


def _adapt_existing(result: Any) -> VenueResult:
    return VenueResult(
        venue=str(result.venue),
        order_id=str(getattr(result, "client_order_id", "") or "") or None,
        status=str(result.status).upper(),
        filled_quantity=D(str(result.filled_quantity)),
        average_price=D(str(result.average_fill_price)) if getattr(result, "average_fill_price", None) is not None else None,
        reason=str(result.reason) if getattr(result, "reason", None) else None,
        reconciled=bool(getattr(result, "reconciled", False)),
    )


async def _place(venue: str, leg: MicroLeg, payload: dict[str, Any], *, gemini: GeminiTradingSession | None, kalshi: KalshiV2Executor | None, polymarket: PolymarketMicroExecutor | None) -> VenueResult:
    if venue == "gemini":
        if gemini is None:
            raise RuntimeError("Gemini executor missing")
        return _adapt_existing(await gemini.place_fok(payload))
    if venue == "kalshi":
        if kalshi is None:
            raise RuntimeError("Kalshi executor missing")
        return _adapt_existing(await kalshi.place(payload))
    if venue == "polymarket":
        if polymarket is None:
            raise RuntimeError("Polymarket executor missing")
        return await polymarket.place_fok(leg)
    raise ValueError("unsupported execution venue")


async def _flatten(venue: str, leg: MicroLeg, payload: dict[str, Any], *, gemini: GeminiTradingSession | None, kalshi: KalshiV2Executor | None, polymarket: PolymarketMicroExecutor | None) -> VenueResult:
    if venue == "gemini":
        if gemini is None:
            raise RuntimeError("Gemini executor missing")
        return _adapt_existing(await gemini.emergency_flatten_market(payload))
    if venue == "kalshi":
        if kalshi is None:
            raise RuntimeError("Kalshi executor missing")
        return _adapt_existing(await kalshi.emergency_flatten(payload))
    if venue == "polymarket":
        if polymarket is None:
            raise RuntimeError("Polymarket executor missing")
        return await polymarket.emergency_flatten(leg)
    raise ValueError("unsupported execution venue")


async def submit_micro_pair(
    plan: MicroExecutionPlan,
    *,
    gemini: GeminiTradingSession | None = None,
    kalshi: KalshiV2Executor | None = None,
    polymarket: PolymarketMicroExecutor | None = None,
) -> dict[str, Any]:
    """Submit both q=1 FOK legs concurrently and reconcile/flatten conservatively."""
    payloads = validate_plan_transport(plan)
    legs = {plan.yes_leg.venue: plan.yes_leg, plan.no_leg.venue: plan.no_leg}
    venues = sorted(legs)

    # Polymarket has a useful exact-position preflight. Gemini/Kalshi ambiguity is
    # reconciled by their existing production transport clients after submission.
    if "polymarket" in legs:
        if polymarket is None:
            raise RuntimeError("Polymarket executor missing")
        await polymarket.preflight(legs["polymarket"])

    results = await asyncio.gather(*(
        _place(v, legs[v], payloads[v], gemini=gemini, kalshi=kalshi, polymarket=polymarket)
        for v in venues
    ))
    by_venue = {result.venue: result for result in results}

    if any(result.ambiguous for result in results):
        return {
            "outcome": "AMBIGUOUS_SUBMISSION",
            "results": {v: r.as_dict() for v, r in by_venue.items()},
            "flatten": None,
            "hard_halt_required": True,
        }

    filled = [result for result in results if result.filled_one]
    if len(filled) == 2:
        outcome = "BOTH_FILLED"
    elif not filled:
        outcome = "NEITHER_FILLED"
    else:
        outcome = "ONE_LEG_FILLED"

    report: dict[str, Any] = {
        "outcome": outcome,
        "results": {v: r.as_dict() for v, r in by_venue.items()},
        "flatten": None,
        "hard_halt_required": outcome == "ONE_LEG_FILLED",
    }
    if len(filled) == 1:
        venue = filled[0].venue
        report["flatten"] = (
            await _flatten(venue, legs[venue], payloads[venue], gemini=gemini, kalshi=kalshi, polymarket=polymarket)
        ).as_dict()
    return report

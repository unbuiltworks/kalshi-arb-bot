"""Bounded Kalshi <-> Polymarket US sports execution transport.

This module is deliberately NOT a scanner.  It can only execute an already-
validated one-contract plan and is fail-closed by design:

* only Kalshi <-> Polymarket US is accepted,
* exactly one contract per leg,
* an explicit execution-approved rule record is required,
* minimum after-fee locked profit and quote-freshness gates are rechecked,
* both entry legs are FOK and submitted concurrently,
* Polymarket orders are marked AUTOMATIC and use synchronous execution,
* both venues are reconciled after ambiguous submissions,
* a one-leg fill triggers a bounded emergency flatten and a hard halt,
* any unresolved ambiguity requires a hard halt rather than a retry.

Nothing in this file creates an ARM token or opts a service into live mode.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal
import os
from pathlib import Path
import time
from typing import Any, Mapping
import uuid

import httpx
from polymarket_us import PolymarketUS

from src.auth import KalshiClient
from src.three_venue_pricing_core import PricedQuote
from src.universal_prediction_quant import LockOpportunity

D = Decimal
ONE = D("1")
KALSHI_PROD_REST = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_MIN_PROFIT = D("0.03")
DEFAULT_MAX_QUOTE_SKEW_MS = 50
DEFAULT_MAX_BOOK_AGE_MS = 100
DEFAULT_ARM_TOKEN = Path("/data/.sports_kp_arm_once")


@dataclass(frozen=True)
class RuleApproval:
    settlement_key: str
    polymarket_slug: str
    kalshi_ticker: str
    polymarket_source_side: str
    kalshi_source_side: str
    rule_fingerprint_sha256: str
    approved_for_execution: bool

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "RuleApproval":
        return cls(
            settlement_key=str(row.get("settlement_key") or ""),
            polymarket_slug=str(row.get("polymarket_slug") or ""),
            kalshi_ticker=str(row.get("kalshi_ticker") or ""),
            polymarket_source_side=str(row.get("polymarket_source_side") or ""),
            kalshi_source_side=str(row.get("kalshi_source_side") or ""),
            rule_fingerprint_sha256=str(row.get("rule_fingerprint_sha256") or ""),
            approved_for_execution=bool(row.get("approved_for_execution")),
        )

    def validate(self) -> None:
        if not self.approved_for_execution:
            raise PermissionError("rule approval is not execution-approved")
        if not self.settlement_key or not self.polymarket_slug or not self.kalshi_ticker:
            raise ValueError("rule approval is missing exact market identity")
        if self.polymarket_source_side not in {"YES", "NO", "CONTRACT"}:
            raise ValueError("invalid Polymarket source side")
        if self.kalshi_source_side not in {"YES", "NO", "CONTRACT"}:
            raise ValueError("invalid Kalshi source side")
        fingerprint = self.rule_fingerprint_sha256.lower()
        if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
            raise ValueError("rule approval requires a SHA-256 fingerprint")


@dataclass(frozen=True)
class ExecutionLeg:
    venue: str
    market_id: str
    normalized_outcome: str
    native_outcome: str
    raw_outcome_price: Decimal
    yes_book_limit: Decimal
    source_side: str

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["raw_outcome_price"] = str(self.raw_outcome_price)
        row["yes_book_limit"] = str(self.yes_book_limit)
        return row


@dataclass(frozen=True)
class KPExecutionPlan:
    settlement_key: str
    unit_profit: Decimal
    quote_skew_ms: int
    created_ms: int
    poly: ExecutionLeg
    kalshi: ExecutionLeg
    rule_fingerprint_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "settlement_key": self.settlement_key,
            "unit_profit": str(self.unit_profit),
            "quote_skew_ms": self.quote_skew_ms,
            "created_ms": self.created_ms,
            "polymarket": self.poly.as_dict(),
            "kalshi": self.kalshi.as_dict(),
            "rule_fingerprint_sha256": self.rule_fingerprint_sha256,
        }


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "order_id": self.order_id,
            "status": self.status,
            "filled_quantity": str(self.filled_quantity),
            "average_price": None if self.average_price is None else str(self.average_price),
            "reason": self.reason,
            "reconciled": self.reconciled,
        }


def _base_market_id(market_id: str) -> str:
    return str(market_id).split("#", 1)[0]


def _native_outcome(source_side: str, normalized_outcome: str) -> str:
    if source_side in {"YES", "CONTRACT"}:
        return normalized_outcome
    if source_side == "NO":
        return "NO" if normalized_outcome == "YES" else "YES"
    raise ValueError(f"unsupported source side {source_side!r}")


def _leg(row: PricedQuote, normalized_outcome: str) -> ExecutionLeg:
    if normalized_outcome not in {"YES", "NO"}:
        raise ValueError("normalized outcome must be YES or NO")
    raw = row.raw_yes_ask if normalized_outcome == "YES" else row.raw_no_ask
    native = _native_outcome(row.source_side, normalized_outcome)
    yes_limit = raw if native == "YES" else ONE - raw
    if not D("0.01") <= yes_limit <= D("0.99"):
        raise ValueError("yes-book limit outside exchange price bounds")
    return ExecutionLeg(
        venue=row.quote.venue,
        market_id=_base_market_id(row.quote.market_id),
        normalized_outcome=normalized_outcome,
        native_outcome=native,
        raw_outcome_price=raw,
        yes_book_limit=yes_limit,
        source_side=row.source_side,
    )


def build_kp_plan(
    opportunity: LockOpportunity,
    priced_quotes: list[PricedQuote],
    approval: RuleApproval,
    *,
    now_ms: int | None = None,
    min_unit_profit: Decimal = DEFAULT_MIN_PROFIT,
    max_quote_skew_ms: int = DEFAULT_MAX_QUOTE_SKEW_MS,
    max_book_age_ms: int = DEFAULT_MAX_BOOK_AGE_MS,
) -> KPExecutionPlan:
    """Convert one fee-inclusive hot signal into exact native venue order legs."""
    approval.validate()
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if opportunity.settlement_key != approval.settlement_key:
        raise PermissionError("opportunity is not the approved settlement key")
    if {opportunity.yes_venue, opportunity.no_venue} != {"kalshi", "polymarket"}:
        raise ValueError("execution plan must be exactly Kalshi <-> Polymarket")
    if D(str(opportunity.max_quantity)) < ONE:
        raise ValueError("less than one contract of displayed depth")
    if D(str(opportunity.unit_profit)) < D(min_unit_profit):
        raise ValueError("after-fee edge below live threshold")
    if int(opportunity.quote_skew_ms) > int(max_quote_skew_ms):
        raise ValueError("cross-venue quote skew above live threshold")

    by_venue = {row.quote.venue: row for row in priced_quotes if row.quote.venue in {"kalshi", "polymarket"}}
    if set(by_venue) != {"kalshi", "polymarket"}:
        raise ValueError("exact current priced quote missing for one execution venue")
    for row in by_venue.values():
        if row.quote.settlement_key != opportunity.settlement_key:
            raise ValueError("priced quote settlement key mismatch")
        if now_ms - int(row.quote.received_ms) > int(max_book_age_ms):
            raise ValueError("book is stale at execution gate")

    normalized_by_venue = {
        opportunity.yes_venue: "YES",
        opportunity.no_venue: "NO",
    }
    poly = _leg(by_venue["polymarket"], normalized_by_venue["polymarket"])
    kalshi = _leg(by_venue["kalshi"], normalized_by_venue["kalshi"])

    if poly.market_id != approval.polymarket_slug or kalshi.market_id != approval.kalshi_ticker:
        raise PermissionError("live market IDs do not match the exact audited approval")
    if poly.source_side != approval.polymarket_source_side or kalshi.source_side != approval.kalshi_source_side:
        raise PermissionError("native-side orientation changed since rule approval")

    return KPExecutionPlan(
        settlement_key=opportunity.settlement_key,
        unit_profit=D(str(opportunity.unit_profit)),
        quote_skew_ms=int(opportunity.quote_skew_ms),
        created_ms=now_ms,
        poly=poly,
        kalshi=kalshi,
        rule_fingerprint_sha256=approval.rule_fingerprint_sha256,
    )


def _tick_aligned(value: Decimal, tick: Decimal) -> bool:
    if tick <= 0:
        return False
    return (value / tick) == (value / tick).to_integral_value()


class PolymarketExecutor:
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
        row = (body.get("positions") or {}).get(slug) or {}
        return D(str(row.get("netPosition") or "0"))

    async def preflight(self, leg: ExecutionLeg) -> None:
        market = await asyncio.to_thread(self.client.markets.retrieve_by_slug, leg.market_id)
        min_qty = D(str(market.get("minimumTradeQty") or "1"))
        tick = D(str(market.get("orderPriceMinTickSize") or "0.01"))
        if min_qty > ONE or not _tick_aligned(ONE, min_qty):
            raise ValueError("Polymarket market does not accept exactly one contract")
        if not _tick_aligned(leg.yes_book_limit, tick):
            raise ValueError("Polymarket price is not aligned to current market tick")
        if await self.position(leg.market_id) != 0:
            raise PermissionError("pre-existing Polymarket position blocks bounded recovery")

    def _payload(self, leg: ExecutionLeg) -> dict[str, Any]:
        return {
            "marketSlug": leg.market_id,
            "intent": "ORDER_INTENT_BUY_LONG" if leg.native_outcome == "YES" else "ORDER_INTENT_BUY_SHORT",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(leg.yes_book_limit), "currency": "USD"},
            "quantity": 1,
            "tif": "TIME_IN_FORCE_FILL_OR_KILL",
            "participateDontInitiate": False,
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
            "synchronousExecution": True,
            "maxBlockTime": "2",
        }

    @staticmethod
    def _response_fill(body: Mapping[str, Any]) -> tuple[Decimal, Decimal | None]:
        qty = D("0")
        notional = D("0")
        for execution in body.get("executions") or []:
            order = execution.get("order") or {}
            cumulative = D(str(order.get("cumQuantity") or "0"))
            if cumulative > qty:
                qty = cumulative
                avg = (order.get("avgPx") or {}).get("value")
                if avg is not None:
                    notional = qty * D(str(avg))
        return qty, (notional / qty if qty > 0 and notional > 0 else None)

    async def place_fok(self, leg: ExecutionLeg) -> VenueResult:
        payload = self._payload(leg)
        try:
            body = await asyncio.to_thread(self.client.orders.create, payload)
            order_id = str(body.get("id") or "") or None
            qty, avg = self._response_fill(body)
            if qty == ONE:
                return VenueResult("polymarket", order_id, "FILLED", qty, avg, None)
            # Synchronous FOK should already be terminal; retrieve if an ID exists.
            if order_id:
                try:
                    row = await asyncio.to_thread(self.client.orders.retrieve, order_id)
                    qty = D(str(row.get("cumQuantity") or "0"))
                    state = str(row.get("state") or "UNKNOWN")
                    avg_value = (row.get("avgPx") or {}).get("value")
                    avg = D(str(avg_value)) if avg_value is not None and qty > 0 else None
                    return VenueResult("polymarket", order_id, state, qty, avg, None, True)
                except Exception:
                    pass
            return VenueResult("polymarket", order_id, "CANCELED", D("0"), None, "FOK_NOT_FILLED")
        except Exception as exc:
            # Do not retry a create whose acknowledgement is ambiguous. Reconcile
            # through the preflight-zero position and then hard-halt upstream.
            try:
                pos = await self.position(leg.market_id)
                expected = ONE if leg.native_outcome == "YES" else -ONE
                if pos == expected:
                    return VenueResult("polymarket", None, "FILLED", ONE, None, "RECONCILED_POSITION", True)
            except Exception:
                pass
            return VenueResult("polymarket", None, "UNKNOWN", D("0"), None, f"{type(exc).__name__}:{str(exc)[:200]}")

    async def emergency_flatten(self, leg: ExecutionLeg) -> VenueResult:
        try:
            # Preflight requires zero starting position, so close_position can only
            # flatten the one contract created by this attempt.
            await asyncio.to_thread(
                self.client.orders.close_position,
                {
                    "marketSlug": leg.market_id,
                    "slippageTolerance": {
                        "currentPrice": {"value": str(leg.yes_book_limit), "currency": "USD"},
                        "ticks": 20,
                    },
                },
            )
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if await self.position(leg.market_id) == 0:
                    return VenueResult("polymarket", None, "FLATTENED", ONE, None, None, True)
                await asyncio.sleep(0.1)
            return VenueResult("polymarket", None, "UNKNOWN", D("0"), None, "FLATTEN_POSITION_NOT_ZERO")
        except Exception as exc:
            return VenueResult("polymarket", None, "UNKNOWN", D("0"), None, f"FLATTEN_{type(exc).__name__}:{str(exc)[:160]}")


class KalshiExecutor:
    def __init__(self, client: KalshiClient | None = None) -> None:
        self.client = client or KalshiClient(base_url=KALSHI_PROD_REST)
        self._owns = client is None

    async def close(self) -> None:
        if self._owns:
            await self.client.close()

    async def position(self, ticker: str) -> Decimal:
        response = await self.client.get("/portfolio/positions", params={"ticker": ticker, "limit": 10})
        response.raise_for_status()
        rows = response.json().get("market_positions") or []
        for row in rows:
            if str(row.get("ticker") or "") == ticker:
                return D(str(row.get("position_fp") or "0"))
        return D("0")

    async def preflight(self, leg: ExecutionLeg) -> None:
        status = await self.client.get("/exchange/status")
        status.raise_for_status()
        if await self.position(leg.market_id) != 0:
            raise PermissionError("pre-existing Kalshi position blocks bounded recovery")

    @staticmethod
    def _payload(leg: ExecutionLeg) -> dict[str, Any]:
        return {
            "ticker": leg.market_id,
            "client_order_id": f"sports-kp-{uuid.uuid4().hex[:20]}",
            "side": "bid" if leg.native_outcome == "YES" else "ask",
            "count": "1.00",
            "price": f"{leg.yes_book_limit:.4f}",
            "time_in_force": "fill_or_kill",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": True,
            "reduce_only": False,
            "subaccount": 0,
            "exchange_index": 0,
        }

    async def _reconcile(self, payload: Mapping[str, Any]) -> VenueResult | None:
        try:
            response = await self.client.get("/portfolio/orders", params={"ticker": payload["ticker"], "limit": 100})
            if response.status_code != 200:
                return None
            for row in response.json().get("orders") or []:
                if str(row.get("client_order_id") or "") != str(payload["client_order_id"]):
                    continue
                qty = D(str(row.get("fill_count_fp") or row.get("fill_count") or "0"))
                cost = D(str(row.get("taker_fill_cost_dollars") or "0"))
                avg = cost / qty if qty > 0 and cost > 0 else None
                return VenueResult(
                    "kalshi", str(row.get("order_id") or "") or None,
                    "FILLED" if qty == ONE else "CANCELED", qty, avg,
                    "RECONCILED_CLIENT_ORDER_ID", True,
                )
        except Exception:
            return None
        return None

    async def place_fok(self, leg: ExecutionLeg) -> VenueResult:
        payload = self._payload(leg)
        try:
            response = await self.client.post("/portfolio/events/orders", json=payload)
        except (httpx.TimeoutException, TimeoutError, asyncio.TimeoutError) as exc:
            recovered = await self._reconcile(payload)
            if recovered is not None:
                return recovered
            return VenueResult("kalshi", None, "UNKNOWN", D("0"), None, f"{type(exc).__name__}:AMBIGUOUS_SUBMISSION")
        if response.status_code != 201:
            return VenueResult("kalshi", None, "REJECTED", D("0"), None, f"HTTP_{response.status_code}")
        body = response.json()
        qty = D(str(body.get("fill_count") or body.get("fill_count_fp") or "0"))
        avg_raw = body.get("average_fill_price")
        avg = D(str(avg_raw)) if avg_raw is not None and qty > 0 else None
        return VenueResult(
            "kalshi", str(body.get("order_id") or "") or None,
            "FILLED" if qty == ONE else "CANCELED", qty, avg,
            None if qty == ONE else "FOK_NOT_FILLED",
        )

    async def emergency_flatten(self, leg: ExecutionLeg) -> VenueResult:
        # Return the position to its preflight-zero state. Ask at 0.01 closes a YES
        # long; bid at 0.99 closes a NO long. IOC + reduce_only prevents expansion.
        payload = {
            "ticker": leg.market_id,
            "client_order_id": f"flatten-k-{uuid.uuid4().hex[:20]}",
            "side": "ask" if leg.native_outcome == "YES" else "bid",
            "count": "1.00",
            "price": "0.0100" if leg.native_outcome == "YES" else "0.9900",
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": True,
            "reduce_only": True,
            "subaccount": 0,
            "exchange_index": 0,
        }
        try:
            response = await self.client.post("/portfolio/events/orders", json=payload)
            if response.status_code != 201:
                return VenueResult("kalshi", None, "UNKNOWN", D("0"), None, f"FLATTEN_HTTP_{response.status_code}")
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if await self.position(leg.market_id) == 0:
                    return VenueResult("kalshi", str(response.json().get("order_id") or "") or None, "FLATTENED", ONE, None, None, True)
                await asyncio.sleep(0.1)
            return VenueResult("kalshi", None, "UNKNOWN", D("0"), None, "FLATTEN_POSITION_NOT_ZERO")
        except Exception as exc:
            return VenueResult("kalshi", None, "UNKNOWN", D("0"), None, f"FLATTEN_{type(exc).__name__}:{str(exc)[:160]}")


async def execute_kp_plan(
    plan: KPExecutionPlan,
    *,
    arm_token: Path = DEFAULT_ARM_TOKEN,
    live_requested: bool = False,
    poly: PolymarketExecutor | None = None,
    kalshi: KalshiExecutor | None = None,
) -> dict[str, Any]:
    """Consume a one-use ARM token and submit exactly one FOK pair."""
    if not live_requested or os.getenv("ENABLE_SPORTS_KP_LIVE", "NO").strip().upper() != "YES":
        raise PermissionError("sports K/P live execution is disabled")
    if not arm_token.is_file():
        raise PermissionError("single-use sports ARM token is absent")
    if plan.unit_profit < DEFAULT_MIN_PROFIT or plan.quote_skew_ms > DEFAULT_MAX_QUOTE_SKEW_MS:
        raise PermissionError("plan no longer meets immutable first-live gates")
    if int(time.time() * 1000) - plan.created_ms > DEFAULT_MAX_BOOK_AGE_MS:
        raise PermissionError("plan expired before send")

    poly = poly or PolymarketExecutor()
    kalshi = kalshi or KalshiExecutor()
    owns_poly = poly is not None
    owns_kalshi = kalshi is not None
    try:
        await asyncio.gather(poly.preflight(plan.poly), kalshi.preflight(plan.kalshi))
        # Consume authorization before the first mutation. Restart/redeploy cannot
        # silently create another submission attempt.
        try:
            arm_token.unlink()
        except FileNotFoundError as exc:
            raise PermissionError("ARM token disappeared before send") from exc

        p_result, k_result = await asyncio.gather(
            poly.place_fok(plan.poly),
            kalshi.place_fok(plan.kalshi),
        )
        ambiguous = p_result.status == "UNKNOWN" or k_result.status == "UNKNOWN"
        p_fill, k_fill = p_result.filled_one, k_result.filled_one
        flatten: VenueResult | None = None

        if ambiguous:
            outcome = "AMBIGUOUS_SUBMISSION"
            hard_halt = True
        elif p_fill and k_fill:
            outcome = "BOTH_FILLED"
            hard_halt = False
        elif not p_fill and not k_fill:
            outcome = "NEITHER_FILLED"
            hard_halt = False
        else:
            outcome = "ONE_LEG_FILLED"
            hard_halt = True
            flatten = await (poly.emergency_flatten(plan.poly) if p_fill else kalshi.emergency_flatten(plan.kalshi))

        return {
            "outcome": outcome,
            "hard_halt_required": hard_halt,
            "single_use_arm_consumed": True,
            "plan": plan.as_dict(),
            "polymarket": p_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None if flatten is None else flatten.as_dict(),
        }
    finally:
        try:
            poly.close()
        except Exception:
            pass
        try:
            await kalshi.close()
        except Exception:
            pass

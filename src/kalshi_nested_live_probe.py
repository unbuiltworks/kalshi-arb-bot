"""Dormant one-shot executor for a first same-event nested Kalshi probe.

DANGEROUS MODULE: this file contains real order transport, but it is deliberately
inert unless BOTH ``--live`` and ``ENABLE_KALSHI_NESTED_LIVE_PROBE=YES`` are
present.  It is kept on a branch separate from all running shadow services.

Safety model:
- exactly 1.00 maker contract, post-only, one allowed ETH/SOL same-event pair;
- wait for authenticated fill + orderbook subscriptions and both snapshots;
- revalidate exact 3c+ one-contract economics from outbound payloads;
- maker must still be a valid inside-spread quote and hedge depth >= 1;
- any maker fill fragment immediately starts cancellation of the remaining maker;
- every maker fill fragment is FOK-hedged independently (fractional fills exist);
- FOK response is checked for exact fill_count/remaining_count; POST != fill;
- timeout cancellation is reconciled against fill stream; ambiguity halts loudly;
- one probe maximum, then process exits.

This is engineering infrastructure, not a profitability claim.  The maker leg can
fill while the hedge moves; a real probe still has leg risk.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import logging
import os
import time
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.kalshi_nested_live_probe_guard import (
    D,
    NestedProbePlan,
    ProbeLeg,
    build_v2_buy_payload,
    live_probe_authorized,
    validate_and_build_payloads,
)
from src.kalshi_nested_trade_through_shadow import _ws_header_kw

logger = logging.getLogger("kalshi_arb.kalshi_nested_live_probe")
NY = ZoneInfo("America/New_York")
PROD_REST = "https://external-api.kalshi.com/trade-api/v2"
PROD_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_SIGN_PATH = "/trade-api/ws/v2"
ORDER_PATH = "/portfolio/events/orders"
MAX_MAKER_WAIT_SECONDS = 120.0
MAX_WS_RECONCILE_SECONDS = 1.5
MAX_FRAGMENT_QUANTITY = D("1.00")
MIN_FRAGMENT_QUANTITY = D("0.01")


@dataclass(frozen=True)
class OrderCreateState:
    order_id: str
    fill_count: Decimal
    remaining_count: Decimal
    ts_ms: int | None


@dataclass(frozen=True)
class HedgeResult:
    quantity: Decimal
    hedge_cost: Decimal
    order_id: str
    exchange_ts_ms: int | None
    send_wall_ms: int
    receive_wall_ms: int


def parse_decimal(value: Any, *, field: str) -> Decimal:
    try:
        return D(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal {field}={value!r}") from exc


def parse_create_state(payload: dict[str, Any], *, max_quantity: Decimal = D("1.00")) -> OrderCreateState:
    order_id = str(payload.get("order_id") or "")
    if not order_id:
        raise ValueError("V2 order response missing order_id")
    fill = parse_decimal(payload.get("fill_count"), field="fill_count")
    remaining = parse_decimal(payload.get("remaining_count"), field="remaining_count")
    if fill < 0 or remaining < 0 or fill + remaining > max_quantity:
        raise ValueError("V2 order response violates probe quantity cap")
    ts_raw = payload.get("ts_ms")
    return OrderCreateState(
        order_id=order_id,
        fill_count=fill,
        remaining_count=remaining,
        ts_ms=int(ts_raw) if ts_raw is not None else None,
    )


def assert_fok_filled(
    payload: dict[str, Any],
    *,
    expected_quantity: Decimal,
) -> OrderCreateState:
    state = parse_create_state(payload, max_quantity=expected_quantity)
    if state.fill_count != expected_quantity or state.remaining_count != 0:
        raise RuntimeError(
            f"FOK hedge did not fully fill: expected={expected_quantity} "
            f"fill={state.fill_count} remaining={state.remaining_count}"
        )
    return state


def fill_fragment_quantity(msg: dict[str, Any], *, maker_order_id: str) -> Decimal | None:
    if str(msg.get("order_id") or "") != maker_order_id:
        return None
    quantity = parse_decimal(msg.get("count_fp"), field="count_fp")
    if quantity < MIN_FRAGMENT_QUANTITY or quantity > MAX_FRAGMENT_QUANTITY:
        raise RuntimeError(f"maker fill fragment outside 0.01..1.00: {quantity}")
    return quantity


def purchased_bid(book: KalshiOrderBook, outcome: str) -> Decimal:
    levels = book.yes if outcome.lower() == "yes" else book.no
    return max(levels) if levels else D("0")


def purchased_ask(book: KalshiOrderBook, outcome: str) -> tuple[Decimal, Decimal] | None:
    """Executable buy cost/depth for YES or NO from complementary resting bids."""
    opposite = book.no if outcome.lower() == "yes" else book.yes
    if not opposite:
        return None
    best_opposite_bid = max(opposite)
    return D("1") - best_opposite_bid, opposite[best_opposite_bid]


def maker_quote_is_inside(book: KalshiOrderBook, leg: ProbeLeg) -> bool:
    ask = purchased_ask(book, leg.outcome)
    if ask is None:
        return False
    best_ask, _ = ask
    bid = purchased_bid(book, leg.outcome)
    return bid < leg.cost < best_ask


def conservative_fragment_locked_profit(
    *,
    quantity: Decimal,
    maker_cost: Decimal,
    hedge_cost: Decimal,
    hedge_outcome: str,
    fee_multiplier: Decimal,
) -> Decimal:
    """Conservative fragment minimum P&L using repo's cent-ceiling fee model.

    Current Kalshi fee rounding accumulates across fills; this intentionally uses
    the older whole-cent ceiling helper as a conservative pre-send bound.  Actual
    fill fees must be recorded from the exchange after the probe.
    """
    q = D(quantity)
    if q < MIN_FRAGMENT_QUANTITY or q > MAX_FRAGMENT_QUANTITY:
        raise ValueError("fragment quantity outside first-probe bounds")
    hedge_yes_book_price = D(hedge_cost) if hedge_outcome.lower() == "yes" else D("1") - D(hedge_cost)
    fee = kalshi_quadratic_taker_fee(
        hedge_yes_book_price,
        q,
        fee_multiplier=D(fee_multiplier),
    )
    return q - q * D(maker_cost) - q * D(hedge_cost) - fee


def build_fragment_hedge_payload(
    *,
    base_leg: ProbeLeg,
    quantity: Decimal,
    live_cost: Decimal,
    client_order_id: str,
) -> dict[str, Any]:
    q = D(quantity)
    if q < MIN_FRAGMENT_QUANTITY or q > MAX_FRAGMENT_QUANTITY:
        raise ValueError("fragment quantity outside 0.01..1.00")
    payload = build_v2_buy_payload(
        leg=ProbeLeg(base_leg.ticker, base_leg.outcome, D(live_cost)),
        maker=False,
        client_order_id=client_order_id,
    )
    payload["count"] = f"{q:.2f}"
    return payload


class KalshiV2Transport:
    def __init__(self) -> None:
        if os.getenv("KALSHI_ENV", "").lower() != "prod":
            raise RuntimeError("live nested probe requires KALSHI_ENV=prod")
        if not API_KEY_ID:
            raise RuntimeError("KALSHI_API_KEY_ID is not configured")
        self.private_key = load_private_key()
        self.http = httpx.AsyncClient(timeout=5.0)

    def _headers(self, method: str, short_path: str) -> dict[str, str]:
        return auth_headers(
            self.private_key,
            method,
            f"/trade-api/v2{short_path}",
            API_KEY_ID,
        )

    async def post_order(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
        send_ms = int(time.time() * 1000)
        response = await self.http.post(
            PROD_REST + ORDER_PATH,
            headers=self._headers("POST", ORDER_PATH),
            json=payload,
        )
        receive_ms = int(time.time() * 1000)
        response.raise_for_status()
        return response.json(), send_ms, receive_ms

    async def cancel_order(self, order_id: str) -> tuple[dict[str, Any], int, int]:
        path = f"{ORDER_PATH}/{order_id}"
        send_ms = int(time.time() * 1000)
        response = await self.http.delete(
            PROD_REST + path,
            headers=self._headers("DELETE", path),
        )
        receive_ms = int(time.time() * 1000)
        if response.status_code == 404:
            return {"order_id": order_id, "reduced_by": "0.00", "not_found": True}, send_ms, receive_ms
        response.raise_for_status()
        return response.json(), send_ms, receive_ms

    async def close(self) -> None:
        await self.http.aclose()


def _ws_headers(private_key) -> dict[str, str]:
    return auth_headers(private_key, "GET", WS_SIGN_PATH, API_KEY_ID)


async def _subscribe_and_wait_ready(
    ws,
    *,
    books: dict[str, KalshiOrderBook],
    market_tickers: list[str],
) -> None:
    await ws.send(json.dumps({
        "id": 1,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta", "fill"],
            "market_tickers": market_tickers,
        },
    }))
    subscribed: set[str] = set()
    snapshots: set[str] = set()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        payload = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic())))
        if payload.get("type") == "error":
            raise RuntimeError(f"WS subscription error: {payload.get('msg')}")
        if payload.get("type") == "subscribed":
            subscribed.add(str((payload.get("msg") or {}).get("channel") or ""))
            continue
        if payload.get("type") in {"orderbook_snapshot", "orderbook_delta"}:
            ticker = str((payload.get("msg") or {}).get("market_ticker") or "")
            if ticker in books:
                books[ticker].apply(payload)
                if payload.get("type") == "orderbook_snapshot":
                    snapshots.add(ticker)
        if {"orderbook_delta", "fill"}.issubset(subscribed) and snapshots == set(market_tickers):
            return
    raise TimeoutError("did not receive both subscriptions and both orderbook snapshots")


def _current_live_hedge(
    *,
    books: dict[str, KalshiOrderBook],
    plan: NestedProbePlan,
    quantity: Decimal,
    require_target: bool,
) -> tuple[Decimal, Decimal]:
    top = purchased_ask(books[plan.hedge.ticker], plan.hedge.outcome)
    if top is None:
        raise RuntimeError("no executable hedge ask")
    cost, depth = top
    if depth < quantity:
        raise RuntimeError(f"hedge depth {depth} < required fragment {quantity}")
    modeled = conservative_fragment_locked_profit(
        quantity=quantity,
        maker_cost=plan.maker.cost,
        hedge_cost=cost,
        hedge_outcome=plan.hedge.outcome,
        fee_multiplier=plan.fee_multiplier,
    )
    if require_target and modeled < plan.target_profit:
        raise RuntimeError(
            f"live hedge no longer preserves target: modeled={modeled} target={plan.target_profit}"
        )
    return cost, modeled


async def _hedge_fragment(
    *,
    transport: KalshiV2Transport,
    books: dict[str, KalshiOrderBook],
    plan: NestedProbePlan,
    quantity: Decimal,
    fragment_index: int,
    require_target: bool,
) -> HedgeResult:
    hedge_cost, modeled = _current_live_hedge(
        books=books,
        plan=plan,
        quantity=quantity,
        require_target=require_target,
    )
    payload = build_fragment_hedge_payload(
        base_leg=plan.hedge,
        quantity=quantity,
        live_cost=hedge_cost,
        client_order_id=f"nested-probe-hedge-{int(time.time()*1000)}-{fragment_index}",
    )
    response, send_ms, receive_ms = await transport.post_order(payload)
    state = assert_fok_filled(response, expected_quantity=quantity)
    logger.warning("NESTED_LIVE_HEDGE_FILLED %s", json.dumps({
        "fragment_index": fragment_index,
        "quantity": str(quantity),
        "hedge_cost": str(hedge_cost),
        "modeled_fragment_min_pnl_before_send": str(modeled),
        "order_id": state.order_id,
        "exchange_ts_ms": state.ts_ms,
        "send_wall_ms": send_ms,
        "receive_wall_ms": receive_ms,
        "request_round_trip_ms": receive_ms - send_ms,
    }, separators=(",", ":")))
    return HedgeResult(
        quantity=quantity,
        hedge_cost=hedge_cost,
        order_id=state.order_id,
        exchange_ts_ms=state.ts_ms,
        send_wall_ms=send_ms,
        receive_wall_ms=receive_ms,
    )


async def run_live_probe(
    plan: NestedProbePlan,
    *,
    cli_live: bool,
    maker_wait_seconds: float = 60.0,
) -> dict[str, Any]:
    """Execute at most one real 1.00-contract maker-first nested probe."""
    # Authorization MUST be checked before loading credentials or opening sockets.
    if not live_probe_authorized(cli_live):
        raise RuntimeError(
            "LIVE DISABLED: require --live AND ENABLE_KALSHI_NESTED_LIVE_PROBE=YES"
        )
    if maker_wait_seconds <= 0 or maker_wait_seconds > MAX_MAKER_WAIT_SECONDS:
        raise ValueError(f"maker_wait_seconds must be 0..{MAX_MAKER_WAIT_SECONDS}")

    now = datetime.now(NY)
    maker_payload, _, locked = validate_and_build_payloads(plan, now=now)
    transport = KalshiV2Transport()
    books = {
        plan.maker.ticker: KalshiOrderBook(plan.maker.ticker),
        plan.hedge.ticker: KalshiOrderBook(plan.hedge.ticker),
    }
    maker_order_id = ""
    cancel_task: asyncio.Task | None = None
    total_maker_filled = D("0")
    total_hedged = D("0")
    hedge_results: list[HedgeResult] = []
    fill_seen: set[str] = set()

    try:
        headers = _ws_headers(transport.private_key)
        async with websockets.connect(
            PROD_WS,
            open_timeout=5.0,
            **_ws_header_kw(headers),
        ) as ws:
            await _subscribe_and_wait_ready(
                ws,
                books=books,
                market_tickers=[plan.maker.ticker, plan.hedge.ticker],
            )

            if not maker_quote_is_inside(books[plan.maker.ticker], plan.maker):
                raise RuntimeError("maker quote is no longer strictly inside spread")
            live_hedge_cost, live_modeled = _current_live_hedge(
                books=books,
                plan=plan,
                quantity=D("1"),
                require_target=True,
            )
            if live_hedge_cost != plan.hedge.cost:
                # Exact full-probe economics were validated at plan.hedge.cost. Do
                # not silently trade a different initial state even if still good.
                raise RuntimeError(
                    f"hedge moved before maker send: planned={plan.hedge.cost} live={live_hedge_cost}"
                )

            logger.warning("NESTED_LIVE_ARMED %s", json.dumps({
                "event_ticker": plan.event_ticker,
                "maker_ticker": plan.maker.ticker,
                "maker_outcome": plan.maker.outcome,
                "maker_cost": str(plan.maker.cost),
                "hedge_ticker": plan.hedge.ticker,
                "hedge_outcome": plan.hedge.outcome,
                "hedge_cost": str(live_hedge_cost),
                "exact_full_probe_locked_profit": str(locked),
                "live_conservative_locked_profit": str(live_modeled),
                "quantity_cap": "1.00",
            }, separators=(",", ":")))

            maker_response, maker_send_ms, maker_receive_ms = await transport.post_order(maker_payload)
            maker_state = parse_create_state(maker_response)
            maker_order_id = maker_state.order_id
            logger.warning("NESTED_LIVE_MAKER_SENT %s", json.dumps({
                "order_id": maker_order_id,
                "fill_count_in_response": str(maker_state.fill_count),
                "remaining_count_in_response": str(maker_state.remaining_count),
                "exchange_ts_ms": maker_state.ts_ms,
                "send_wall_ms": maker_send_ms,
                "receive_wall_ms": maker_receive_ms,
                "request_round_trip_ms": maker_receive_ms - maker_send_ms,
            }, separators=(",", ":")))

            # If post-only was immediately cancelled instead of resting, no money
            # was put at risk and the probe is complete.
            if maker_state.fill_count == 0 and maker_state.remaining_count == 0:
                return {"status": "POST_ONLY_CANCELLED", "maker_order_id": maker_order_id}

            deadline = time.monotonic() + maker_wait_seconds
            timeout_cancel_started = False
            cancel_reduced_by: Decimal | None = None
            reconcile_deadline: float | None = None

            while True:
                now_mono = time.monotonic()
                if now_mono >= deadline and not timeout_cancel_started and cancel_task is None:
                    timeout_cancel_started = True
                    cancel_task = asyncio.create_task(transport.cancel_order(maker_order_id))
                    reconcile_deadline = now_mono + MAX_WS_RECONCILE_SECONDS

                # Successful full hedge is terminal even if maker cancel races; max
                # maker quantity is 1.00, so no further economic exposure can appear.
                if total_hedged >= D("1.00"):
                    break

                if cancel_task is not None and cancel_task.done() and cancel_reduced_by is None:
                    cancel_payload, cancel_send_ms, cancel_receive_ms = await cancel_task
                    cancel_reduced_by = parse_decimal(cancel_payload.get("reduced_by", "0"), field="reduced_by")
                    logger.warning("NESTED_LIVE_MAKER_CANCEL %s", json.dumps({
                        "order_id": maker_order_id,
                        "reduced_by": str(cancel_reduced_by),
                        "send_wall_ms": cancel_send_ms,
                        "receive_wall_ms": cancel_receive_ms,
                        "request_round_trip_ms": cancel_receive_ms - cancel_send_ms,
                    }, separators=(",", ":")))
                    if reconcile_deadline is None:
                        reconcile_deadline = time.monotonic() + MAX_WS_RECONCILE_SECONDS

                if (
                    cancel_reduced_by is not None
                    and total_maker_filled + cancel_reduced_by == D("1.00")
                    and total_hedged == total_maker_filled
                ):
                    break
                if reconcile_deadline is not None and time.monotonic() >= reconcile_deadline:
                    if total_maker_filled + (cancel_reduced_by or D("0")) != D("1.00"):
                        raise RuntimeError(
                            "maker cancel/fill reconciliation ambiguous; manual position review required"
                        )
                    if total_hedged != total_maker_filled:
                        raise RuntimeError("maker exposure remains unhedged after reconciliation")
                    break

                recv_timeout = 0.10
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
                except asyncio.TimeoutError:
                    continue
                receive_wall_ms = int(time.time() * 1000)
                payload = json.loads(raw)
                if payload.get("type") == "error":
                    raise RuntimeError(f"WS error during live probe: {payload.get('msg')}")
                msg_type = payload.get("type")
                msg = payload.get("msg") or {}
                if msg_type in {"orderbook_snapshot", "orderbook_delta"}:
                    ticker = str(msg.get("market_ticker") or "")
                    if ticker in books:
                        books[ticker].apply(payload)
                    # Before any fill, if the target hedge disappears, immediately
                    # start canceling the maker. After a fill, hedging takes priority.
                    if total_maker_filled == 0 and cancel_task is None:
                        try:
                            _current_live_hedge(
                                books=books,
                                plan=plan,
                                quantity=D("1"),
                                require_target=True,
                            )
                        except RuntimeError as exc:
                            logger.warning("NESTED_LIVE_CANCEL_ON_HEDGE_BREAK reason=%s", str(exc))
                            cancel_task = asyncio.create_task(transport.cancel_order(maker_order_id))
                            reconcile_deadline = time.monotonic() + MAX_WS_RECONCILE_SECONDS
                    continue
                if msg_type != "fill":
                    continue

                quantity = fill_fragment_quantity(msg, maker_order_id=maker_order_id)
                if quantity is None:
                    continue
                trade_id = str(msg.get("trade_id") or "")
                dedupe_key = trade_id or f"{msg.get('ts_ms')}:{quantity}:{total_maker_filled}"
                if dedupe_key in fill_seen:
                    continue
                fill_seen.add(dedupe_key)
                if total_maker_filled + quantity > D("1.00"):
                    raise RuntimeError("maker fills exceed hard 1.00-contract cap")
                total_maker_filled += quantity
                logger.warning("NESTED_LIVE_MAKER_FILL %s", json.dumps({
                    "trade_id": trade_id,
                    "order_id": maker_order_id,
                    "fragment_quantity": str(quantity),
                    "cumulative_maker_filled": str(total_maker_filled),
                    "exchange_ts_ms": msg.get("ts_ms"),
                    "receive_wall_ms": receive_wall_ms,
                }, separators=(",", ":")))

                # First fragment: cancel all unfilled maker quantity concurrently.
                if cancel_task is None:
                    cancel_task = asyncio.create_task(transport.cancel_order(maker_order_id))
                    reconcile_deadline = time.monotonic() + MAX_WS_RECONCILE_SECONDS

                # Protect exposure first. Partial fragments are hedged even though
                # their per-fragment rounding can make P&L worse than the original
                # 3c full-lot target. Never leave a known fill deliberately naked.
                result = await _hedge_fragment(
                    transport=transport,
                    books=books,
                    plan=plan,
                    quantity=quantity,
                    fragment_index=len(hedge_results) + 1,
                    require_target=(quantity == D("1.00")),
                )
                hedge_results.append(result)
                total_hedged += quantity

            status = "HEDGED" if total_hedged == total_maker_filled and total_maker_filled > 0 else "NO_FILL"
            result = {
                "status": status,
                "maker_order_id": maker_order_id,
                "maker_filled": str(total_maker_filled),
                "hedged": str(total_hedged),
                "cancel_reduced_by": str(cancel_reduced_by) if cancel_reduced_by is not None else None,
                "hedge_orders": [x.order_id for x in hedge_results],
                "one_probe_complete": True,
            }
            logger.warning("NESTED_LIVE_DONE %s", json.dumps(result, separators=(",", ":")))
            return result
    finally:
        # Best-effort cancellation if an exception occurs after maker creation.
        if maker_order_id and total_maker_filled < D("1.00"):
            try:
                await transport.cancel_order(maker_order_id)
            except Exception:
                logger.exception("NESTED_LIVE_BEST_EFFORT_CANCEL_FAILED order_id=%s", maker_order_id)
        await transport.close()


def _parse_leg(value: str) -> tuple[str, str, Decimal]:
    # TICKER:yes:0.95
    parts = value.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("leg must be TICKER:yes|no:COST")
    ticker, outcome, cost_raw = parts
    try:
        cost = D(cost_raw)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("invalid leg cost") from exc
    return ticker.upper(), outcome.lower(), cost


def main() -> None:
    parser = argparse.ArgumentParser(description="ONE-SHOT REAL nested Kalshi probe; disabled by default")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--series", required=True, choices=["KXETHD", "KXSOLD"])
    parser.add_argument("--event", required=True)
    parser.add_argument("--event-close", required=True, help="ISO8601 timezone-aware close")
    parser.add_argument("--maker", type=_parse_leg, required=True, help="TICKER:yes|no:COST")
    parser.add_argument("--hedge", type=_parse_leg, required=True, help="TICKER:yes|no:COST")
    parser.add_argument("--fee-type", default="quadratic")
    parser.add_argument("--fee-multiplier", type=Decimal, required=True)
    parser.add_argument("--target-profit", type=Decimal, default=D("0.03"))
    parser.add_argument("--maker-wait-seconds", type=float, default=60.0)
    args = parser.parse_args()

    maker_ticker, maker_outcome, maker_cost = args.maker
    hedge_ticker, hedge_outcome, hedge_cost = args.hedge
    plan = NestedProbePlan(
        series=args.series,
        event_ticker=args.event.upper(),
        event_close=datetime.fromisoformat(args.event_close),
        maker=ProbeLeg(maker_ticker, maker_outcome, maker_cost),
        hedge=ProbeLeg(hedge_ticker, hedge_outcome, hedge_cost),
        fee_type=args.fee_type,
        fee_multiplier=args.fee_multiplier,
        target_profit=args.target_profit,
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    result = asyncio.run(run_live_probe(
        plan,
        cli_live=args.live,
        maker_wait_seconds=args.maker_wait_seconds,
    ))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

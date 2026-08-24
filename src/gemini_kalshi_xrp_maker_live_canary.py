"""Single-attempt XRP 15m Gemini-maker -> Kalshi-FOK live canary.

This is deliberately NOT a continuous production market maker. It is the bounded
bridge that validates maker fill shape and first-fill hedging with real venue
semantics before the strategy is allowed to repeat.

Live mutation requires all three independent conditions:
  ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES
  ENABLE_GEMINI_KALSHI_XRP_MAKER_CANARY=YES
  CRYPTO_MAKER_CANARY_ID == CRYPTO_MAKER_CANARY_ARM != ""

Safety:
- XRP only, q=1 maker order, exactly one campaign ID ever;
- current threshold-dominance authorization and >=0.5bp protective gap;
- highest partial-resilient quote that preserves >=3c/contract for a 0.25 first
  execution after one adverse Kalshi tick and conservative fees;
- Gemini MOC (maker-only) resting order;
- first actual execution immediately triggers remainder cancellation;
- actual fill-event fee fields are accumulated; if REST reports uncaptured fill
  quantity, hedge admission is denied and the Gemini position is flattened;
- exact cumulative fill quantity is hedged on Kalshi FOK only if the actual maker
  fill economics still preserve >=3c/contract at the fresh one-tick-cushioned hedge;
- otherwise exact Gemini quantity is flattened immediately and the process hard-halts;
- Command Center reserves $1 before mutation and enforces the existing $5 realized
  risk-loss ceiling; no budget is reset/increased here;
- one successful two-leg fill is FILLED_NOT_SETTLED, never realized arbitrage.

Settlement remains INDEX/RULE RISK because venue price sources differ.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import time
from typing import Any, Mapping
import uuid

import httpx

from src import gemini_kalshi_crypto_maker_budget as budget
from src import gemini_kalshi_maker_dominance_guard as dominance
from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_rest_guard as rest_guard
from src.gemini_kalshi_aligned_watch import _discover_pairs
from src.gemini_kalshi_current_refdata_probe import _kalshi_market
from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_gemini_maker_partial_economics import _hedge_tick
from src.gemini_kalshi_gemini_maker_safe_quote import choose_quote
from src.gemini_kalshi_live_service import _gemini_available_usd, _gemini_terms_ok
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor
from src.gemini_prediction_auth import authenticated_ws_connect, private_rest_request

D = Decimal
ONE = D("1")
ASSET = "XRP"
MIN_SAFE_FIRST_FILL = D("0.25")
TARGET = D("0.03")
MAX_SKEW_MS = 50
MAX_AGE_MS = 250
REST_SECONDS = 45.0
MIN_SECONDS_TO_EXPIRY = 90.0
GENERAL_LIVE_ENV = "ENABLE_GEMINI_KALSHI_LIVE_PROBES"
CANARY_LIVE_ENV = "ENABLE_GEMINI_KALSHI_XRP_MAKER_CANARY"
CANARY_ID_ENV = "CRYPTO_MAKER_CANARY_ID"
CANARY_ARM_ENV = "CRYPTO_MAKER_CANARY_ARM"


def emit(stage: str, **fields: Any) -> None:
    print("CRYPTO_XRP_MAKER_CANARY " + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str), flush=True)


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    cid = os.getenv(CANARY_ID_ENV, "").strip()
    arm = os.getenv(CANARY_ARM_ENV, "").strip()
    return bool(_enabled(GENERAL_LIVE_ENV) and _enabled(CANARY_LIVE_ENV) and cid and arm == cid)


def trade_id() -> str:
    cid = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in os.getenv(CANARY_ID_ENV, "").strip())[:64]
    return f"crypto-xrp-maker-{cid or 'disarmed'}"


def kalshi_no_fok_payload(*, ticker: str, no_limit: D, quantity: D, client_id: str) -> dict[str, Any]:
    if not (D("0") < no_limit < ONE and D("0") < quantity <= ONE):
        raise ValueError("invalid Kalshi NO FOK inputs")
    yes_space = ONE - no_limit
    return {
        "ticker": ticker,
        "client_order_id": client_id[:64],
        "side": "ask",
        "count": str(quantity),
        "price": str(yes_space.quantize(D("0.0001"))),
        "time_in_force": "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }


@dataclass
class MakerFill:
    quantity: D = D("0")
    notional: D = D("0")
    fees: D = D("0")
    executions: int = 0
    missing_fee_events: int = 0

    def add(self, event: Mapping[str, Any]) -> None:
        status = str(event.get("X") or "").upper()
        if status not in {"PARTIALLY_FILLED", "FILLED"}:
            return
        q = D(str(event.get("Z") or "0"))
        p = D(str(event.get("L") or event.get("p") or "0"))
        if q <= 0:
            return
        self.quantity += q
        self.notional += q * p
        self.executions += 1
        if event.get("n") is None:
            self.missing_fee_events += 1
            self.fees += D("0.01")  # fail-closed per-execution rounding assumption
        else:
            self.fees += max(D("0"), D(str(event.get("n") or "0")))


class GeminiMakerSession:
    """Isolated authenticated Gemini session exposing raw order execution events."""
    def __init__(self) -> None:
        self.ws = None
        self.reader: asyncio.Task | None = None
        self.responses: dict[str, asyncio.Future] = {}
        self.events: asyncio.Queue = asyncio.Queue()
        self.lock = asyncio.Lock()

    async def connect(self) -> None:
        self.ws = await authenticated_ws_connect(timeout=5.0)
        self.reader = asyncio.create_task(self._read())
        ack = await self.request("SUBSCRIBE", ["orders@account"], timeout=5.0)
        if int(ack.get("status", 0)) != 200:
            raise RuntimeError("Gemini orders@account subscription failed")

    async def close(self) -> None:
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def _read(self) -> None:
        assert self.ws is not None
        async for raw in self.ws:
            msg = json.loads(raw)
            rid = str(msg.get("id") or "")
            if rid:
                fut = self.responses.get(rid)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
                continue
            if msg.get("e") == "orderUpdate":
                await self.events.put(msg)

    async def request(self, method: str, params: Any, *, timeout: float = 3.0) -> dict[str, Any]:
        if self.ws is None:
            raise RuntimeError("Gemini maker session not connected")
        rid = f"xrp-mk-{uuid.uuid4().hex}"
        fut = asyncio.get_running_loop().create_future()
        self.responses[rid] = fut
        try:
            async with self.lock:
                await self.ws.send(json.dumps({"id": rid, "method": method, "params": params}, separators=(",", ":")))
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self.responses.pop(rid, None)


async def _order_by_client(client_id: str, symbol: str) -> dict[str, Any] | None:
    now_ms = int(time.time() * 1000)
    queries = [
        ("/v1/prediction-markets/orders/active", {"symbol": symbol, "limit": 100, "offset": 0}),
        ("/v1/prediction-markets/orders/history", {"status": "filled", "symbol": symbol, "limit": 100, "from": now_ms - 120000, "to": now_ms + 1000}),
        ("/v1/prediction-markets/orders/history", {"status": "cancelled", "symbol": symbol, "limit": 100, "from": now_ms - 120000, "to": now_ms + 1000}),
    ]
    for path, params in queries:
        response = await private_rest_request("POST", path, params=params, timeout=4.0)
        if response.status_code != 200:
            continue
        body = response.json()
        for row in body.get("orders", []) if isinstance(body, Mapping) else []:
            if isinstance(row, Mapping) and str(row.get("clientOrderId") or "") == client_id:
                return dict(row)
    return None


def _order_id(row: Mapping[str, Any] | None) -> str | None:
    if not row:
        return None
    value = row.get("orderId") or row.get("order_id") or row.get("id")
    return None if value in (None, "") else str(value)


async def _cancel_maker(session: GeminiMakerSession, *, row: Mapping[str, Any] | None) -> dict[str, Any]:
    oid = _order_id(row)
    if oid:
        try:
            ack = await session.request("order.cancel", {"orderId": oid}, timeout=3.0)
            if int(ack.get("status", 0)) == 200:
                return {"method": "WS_ORDER_CANCEL", "ok": True}
        except Exception:
            pass
        response = await private_rest_request(
            "POST", "/v1/prediction-markets/order/cancel",
            params={"orderId": int(oid) if oid.isdigit() else oid}, timeout=4.0,
        )
        return {"method": "REST_ORDER_CANCEL", "ok": response.status_code == 200, "http": response.status_code}
    try:
        ack = await session.request("order.cancel_session", {}, timeout=3.0)
        return {"method": "WS_CANCEL_SESSION", "ok": int(ack.get("status", 0)) == 200}
    except Exception as exc:
        return {"method": "WS_CANCEL_SESSION", "ok": False, "error": type(exc).__name__}


async def _market_streams(pair: Any):
    queue: asyncio.Queue = asyncio.Queue(maxsize=50_000)
    stats = base.MakerStats(asset=ASSET)
    tasks = [
        asyncio.create_task(base._gemini_stream(pair.gemini_symbol, queue, stats)),
        asyncio.create_task(rest_guard.kalshi_rest_book_stream(pair.kalshi_ticker, queue, stats)),
    ]
    return queue, tasks


async def _fresh_books(queue: asyncio.Queue, *, g: TopOfBook | None = None, k: TopOfBook | None = None, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            kind, _, top = await asyncio.wait_for(queue.get(), timeout=min(0.5, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            continue
        if kind == "gemini": g = top
        elif kind == "kalshi": k = top
        if g is None or k is None:
            continue
        now_ms = int(time.time() * 1000)
        skew = abs(int(g.ts_ms) - int(k.ts_ms))
        age = max(now_ms - int(g.ts_ms), now_ms - int(k.ts_ms))
        if 0 <= age <= MAX_AGE_MS and skew <= MAX_SKEW_MS:
            return g, k
    raise RuntimeError("no fresh <=50ms synchronized books")


async def _flatten_gemini_yes(symbol: str, quantity: D, trade: str) -> dict[str, Any]:
    session = GeminiTradingSession()
    try:
        await session.connect()
        payload = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "timeInForce": "IOC",
            "quantity": str(quantity),
            "eventOutcome": "YES",
            "clientOrderId": f"{trade}-flat"[:36],
        }
        result = await session.place_fok(payload, terminal_timeout=3.0)
        return result.as_dict()
    finally:
        await session.close()


async def _preflight(kalshi: KalshiV2Executor, *, require_funds: bool) -> dict[str, Any]:
    terms_ok = await _gemini_terms_ok()
    gemini_usd = await _gemini_available_usd()
    kbal = await kalshi.client.get("/portfolio/balance")
    kbal.raise_for_status()
    kalshi_usd = D(str(kbal.json().get("balance") or "0")) / D("100")
    if not terms_ok:
        raise RuntimeError("Gemini prediction terms not accepted")
    if require_funds and (gemini_usd < ONE or kalshi_usd < ONE):
        raise RuntimeError("maker canary requires >=$1 available on each venue")
    return {
        "gemini_terms": True,
        "gemini_funded_ge_1": gemini_usd >= ONE,
        "kalshi_funded_ge_1": kalshi_usd >= ONE,
        "credentials_ok": True,
    }


async def run_once() -> dict[str, Any]:
    armed = live_armed()
    trade = trade_id()
    out: dict[str, Any] = {
        "mode": "LIVE_PROBE" if armed else "READ_ONLY_PREFLIGHT",
        "armed": armed,
        "asset": ASSET,
        "quantity_max": "1",
        "min_safe_first_fill": str(MIN_SAFE_FIRST_FILL),
        "target_per_contract": str(TARGET),
        "classification": budget.CLASSIFICATION,
        "guaranteed_arbitrage": False,
        "order_entry": False,
        "realized": False,
    }

    # Durable campaign idempotency: once Command Center has ever seen this trade id,
    # a redeploy with unchanged arm variables cannot submit again.
    rows = await budget._trades()
    if any(str(row.get("trade_id") or "") == trade for row in rows):
        out["status"] = "DUPLICATE_CAMPAIGN_BLOCKED"
        emit("RESULT", **out)
        return out

    kalshi = KalshiV2Executor()
    market_tasks: list[asyncio.Task] = []
    maker: GeminiMakerSession | None = None
    reserved = False
    pair = None
    try:
        out["preflight"] = await _preflight(kalshi, require_funds=armed)
        async with httpx.AsyncClient(timeout=15.0) as http:
            pairs = await _discover_pairs(http, 30.0)
            future = sorted((p for p in pairs if p.asset == ASSET and p.window_end.timestamp() > time.time()), key=lambda p: p.window_end)
            if not future:
                raise RuntimeError("no current XRP 15m pair")
            pair = future[0]
            seconds_to_expiry = pair.window_end.timestamp() - time.time()
            if seconds_to_expiry < MIN_SECONDS_TO_EXPIRY:
                out["status"] = "TOO_CLOSE_TO_EXPIRY"
                emit("RESULT", **out)
                return out
            fee_meta = await base.fetch_series_fee_metadata(http, base.ASSET_SERIES[ASSET])
            market = await _kalshi_market(http, pair.kalshi_ticker)

        geometry = await dominance.fetch_pair_geometry(pair, ASSET)
        out["geometry"] = geometry
        if not geometry.get("authorized") or str(geometry.get("dominance_safe_direction")) != "GEMINI_YES_KALSHI_NO":
            out["status"] = "DOMINANCE_NOT_AUTHORIZED_FOR_GEMINI_MAKER"
            emit("RESULT", **out)
            return out

        market_queue, market_tasks = await _market_streams(pair)
        g, k = await _fresh_books(market_queue)
        quote = choose_quote(
            maker_best_bid=D(g.yes_bid), maker_best_ask=D(g.yes_ask),
            hedge_observed_price=D(k.no_ask), hedge_outcome="NO",
            kalshi_market=market, fee_meta=fee_meta,
            min_safe_first_fill=MIN_SAFE_FIRST_FILL, target=TARGET,
        )
        if quote is None or D(quote["maker_quote"]) < D(g.yes_bid):
            out["status"] = "NO_PARTIAL_SAFE_QUOTE_AT_OR_ABOVE_BID"
            emit("RESULT", **out)
            return out
        out["quote"] = quote
        candidate = {
            "maker_outcome": "YES", "hedge_outcome": "NO",
            "maker_price": str(quote["maker_quote"]),
            "hedge_price": str(quote["hedge_cushioned_price"]),
            "locked_profit": str(quote["q1_economics"]["locked_profit"]),
        }
        if not armed:
            out["status"] = "READ_ONLY_READY"
            emit("RESULT", **out)
            return out

        await budget.reserve(candidate=candidate, pair=pair, geometry=geometry, trade_id=trade)
        reserved = True
        maker = GeminiMakerSession()
        await maker.connect()
        client_id = f"{trade}-mk"[:36]
        params = {
            "symbol": pair.gemini_symbol,
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "MOC",
            "price": str(quote["maker_quote"]),
            "quantity": "1",
            "eventOutcome": "YES",
            "clientOrderId": client_id,
        }
        out["order_entry"] = True
        ack = await maker.request("order.place", params, timeout=4.0)
        if int(ack.get("status", 0)) != 200:
            await budget.release(pair=pair, trade_id=trade, reason="GEMINI_MAKER_REJECTED_ZERO_FILL")
            reserved = False
            out["status"] = "MAKER_REJECTED_ZERO_FILL"
            out["ack_status"] = int(ack.get("status", 0))
            emit("RESULT", **out)
            return out
        row = None
        for _ in range(10):
            row = await _order_by_client(client_id, pair.gemini_symbol)
            if row is not None: break
            await asyncio.sleep(0.05)
        emit("MAKER_POSTED", trade_id=trade, maker_price=str(quote["maker_quote"]), order_recovered=bool(row), order_entry=True)

        fills = MakerFill()
        first_fill_seen = False
        started = time.monotonic()
        cancel_reason = "REST_TIMEOUT"
        while time.monotonic() - started < REST_SECONDS:
            market_get = asyncio.create_task(market_queue.get())
            fill_get = asyncio.create_task(maker.events.get())
            done, pending = await asyncio.wait({market_get, fill_get}, timeout=0.20, return_when=asyncio.FIRST_COMPLETED)
            for task in pending: task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                continue
            item = next(iter(done)).result()
            if isinstance(item, tuple) and len(item) == 3:
                kind, _, top = item
                if kind == "gemini": g = top
                elif kind == "kalshi": k = top
                now_ms = int(time.time() * 1000)
                if g is None or k is None or max(now_ms-int(g.ts_ms), now_ms-int(k.ts_ms)) > MAX_AGE_MS or abs(int(g.ts_ms)-int(k.ts_ms)) > MAX_SKEW_MS:
                    cancel_reason = "BOOK_FRESHNESS_LOST"
                    break
                tick = _hedge_tick(market, "NO", D(k.no_ask))
                hedge_limit = min(D("0.999"), D(k.no_ask) + tick)
                from src.gemini_kalshi_gemini_maker_safe_quote import economics
                live_floor = economics(maker_price=D(quote["maker_quote"]), hedge_price=hedge_limit, quantity=MIN_SAFE_FIRST_FILL, fee_meta=fee_meta)
                if live_floor["locked_per_contract"] < TARGET:
                    cancel_reason = "PARTIAL_SAFE_FLOOR_DROPPED"
                    break
            elif isinstance(item, Mapping):
                if str(item.get("c") or "") != client_id:
                    continue
                before = fills.quantity
                fills.add(item)
                if fills.quantity > before:
                    first_fill_seen = True
                    cancel_reason = "FIRST_EXECUTION_FREEZE"
                    break

        cancel = await _cancel_maker(maker, row=row)
        # Drain any execution events already delivered during the cancel race.
        await asyncio.sleep(0.05)
        while not maker.events.empty():
            evt = maker.events.get_nowait()
            if isinstance(evt, Mapping) and str(evt.get("c") or "") == client_id:
                fills.add(evt)
        final_row = await _order_by_client(client_id, pair.gemini_symbol)
        rest_filled = D(str((final_row or {}).get("filledQuantity") or "0"))
        if not first_fill_seen and rest_filled <= 0:
            if not cancel.get("ok"):
                out["status"] = "ZERO_FILL_CANCEL_AMBIGUOUS_HARD_HALT"
                await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=0, metadata={"cancel": cancel})
                emit("RESULT", **out)
                return out
            await budget.release(pair=pair, trade_id=trade, reason=cancel_reason)
            reserved = False
            out.update({"status": "MAKER_CANCELED_ZERO_FILL", "cancel_reason": cancel_reason, "cancel": cancel})
            emit("RESULT", **out)
            return out

        quantity = max(rest_filled, fills.quantity)
        if quantity <= 0 or quantity > ONE:
            out["status"] = "MAKER_FILL_QUANTITY_AMBIGUOUS_HARD_HALT"
            await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=1, metadata={"ws_qty": str(fills.quantity), "rest_qty": str(rest_filled)})
            emit("RESULT", **out)
            return out

        uncaptured = rest_filled > fills.quantity + D("0.000001")
        if uncaptured:
            flatten = await _flatten_gemini_yes(pair.gemini_symbol, quantity, trade)
            out.update({"status": "UNCAPTURED_CANCEL_RACE_FILL_FLATTENED_HARD_HALT", "flatten": flatten, "filled_quantity": str(quantity)})
            await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=1, metadata={"filled_quantity": str(quantity), "flatten": flatten})
            emit("RESULT", **out)
            return out

        g, k = await _fresh_books(market_queue, g=g, k=k, timeout=2.0)
        if D(k.no_ask_size) < quantity:
            flatten = await _flatten_gemini_yes(pair.gemini_symbol, quantity, trade)
            out.update({"status": "INSUFFICIENT_KALSHI_DEPTH_FLATTENED_HARD_HALT", "flatten": flatten, "filled_quantity": str(quantity)})
            await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=1, metadata={"filled_quantity": str(quantity), "flatten": flatten})
            emit("RESULT", **out)
            return out

        tick = _hedge_tick(market, "NO", D(k.no_ask))
        hedge_limit = min(D("0.999"), D(k.no_ask) + tick)
        hedge_fee = fee_meta.taker_fee_fn()(hedge_limit, quantity)
        maker_notional = fills.notional if fills.notional > 0 else quantity * D(str((final_row or {}).get("avgExecutionPrice") or quote["maker_quote"]))
        maker_fees = fills.fees if fills.executions > 0 else D("0.01")
        locked = quantity - maker_notional - maker_fees - quantity * hedge_limit - hedge_fee
        locked_per_contract = locked / quantity
        actual_econ = {
            "quantity": str(quantity), "maker_notional": str(maker_notional),
            "maker_fee_observed_or_conservative": str(maker_fees), "maker_execution_events": fills.executions,
            "missing_fee_events": fills.missing_fee_events, "hedge_limit": str(hedge_limit),
            "hedge_fee_conservative": str(hedge_fee), "locked_profit": str(locked),
            "locked_per_contract": str(locked_per_contract),
        }
        out["actual_fill_economics"] = actual_econ
        if locked_per_contract < TARGET:
            flatten = await _flatten_gemini_yes(pair.gemini_symbol, quantity, trade)
            out.update({"status": "FIRST_FILL_BELOW_3C_FLATTENED_HARD_HALT", "flatten": flatten, "filled_quantity": str(quantity)})
            await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=1, metadata={**actual_econ, "flatten": flatten})
            emit("RESULT", **out)
            return out

        hedge_payload = kalshi_no_fok_payload(
            ticker=pair.kalshi_ticker, no_limit=hedge_limit, quantity=quantity,
            client_id=f"{trade}-kh",
        )
        hedge = await kalshi.place(hedge_payload)
        hedge_filled = D(str(hedge.filled_quantity or "0"))
        if hedge.status != "FILLED" or hedge_filled != quantity:
            flatten = await _flatten_gemini_yes(pair.gemini_symbol, quantity, trade)
            out.update({"status": "KALSHI_HEDGE_FAILED_GEMINI_FLATTENED_HARD_HALT", "hedge": hedge.as_dict(), "flatten": flatten, "filled_quantity": str(quantity)})
            await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=1, metadata={**actual_econ, "hedge": hedge.as_dict(), "flatten": flatten})
            emit("RESULT", **out)
            return out

        out.update({
            "status": "BOTH_FILLED_RECONCILED_NOT_SETTLED",
            "hedge": hedge.as_dict(), "filled_quantity": str(quantity),
            "counts_as_realized_arb": False, "realized": False,
        })
        await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=2, metadata={**actual_econ, "hedge": hedge.as_dict(), "settlement_pending": True})
        emit("RESULT", **out)
        return out
    except Exception as exc:
        out.update({"status": "HARD_HALT_EXCEPTION", "error": f"{type(exc).__name__}:{str(exc)[:220]}"})
        if reserved and pair is not None:
            try:
                await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=0, metadata={"error_type": type(exc).__name__})
            except Exception:
                pass
        emit("RESULT", **out)
        return out
    finally:
        for task in market_tasks:
            task.cancel()
        await asyncio.gather(*market_tasks, return_exceptions=True)
        if maker is not None:
            await maker.close()
        await kalshi.close()


if __name__ == "__main__":
    asyncio.run(run_once())

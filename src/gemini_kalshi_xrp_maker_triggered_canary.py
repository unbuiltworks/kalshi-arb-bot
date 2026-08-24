"""Bounded XRP Gemini-maker -> Kalshi-FOK canary with atomic paired books.

A public Gemini bookTicker update is the trigger for an immediate exact Kalshi
REST depth-1 read. The Kalshi quote timestamp is the HTTP request start, never the
response arrival. Only already-paired snapshots with <=50 ms skew and <=250 ms age
enter admission or the resting-order monitor. This preserves the existing safety
limits without evaluating transient mixed-age books.

This lane remains threshold-dominance INDEX/RULE RISK, not guaranteed arbitrage.
A successful two-leg execution is FILLED_NOT_SETTLED until authoritative settlement.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import time
from typing import Any, Mapping

import httpx
import websockets

from src import gemini_kalshi_crypto_maker_budget as budget
from src import gemini_kalshi_maker_dominance_guard as dominance
from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_rest_guard as rest_guard
from src import gemini_kalshi_xrp_maker_live_canary as canary
from src.auth import KalshiClient
from src.gemini_kalshi_aligned_watch import _discover_pairs
from src.gemini_kalshi_current_refdata_probe import _kalshi_market
from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_gemini_maker_partial_economics import _hedge_tick
from src.gemini_kalshi_gemini_maker_safe_quote import choose_quote, economics
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor, KALSHI_PROD_REST

D = Decimal
ONE = D("1")
ASSET = "XRP"
TARGET = D("0.03")
MIN_SAFE_FIRST_FILL = D("0.25")
MAX_SKEW_MS = 50
MAX_AGE_MS = 250
MIN_TRIGGER_MS = 100
WAIT_SECONDS = float(os.getenv("CRYPTO_MAKER_FILL_WAIT_SECONDS", "90"))
REST_SECONDS = 45.0
MIN_SECONDS_TO_EXPIRY = 90.0


def emit(stage: str, **fields: Any) -> None:
    print("CRYPTO_XRP_TRIGGERED_CANARY " + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str), flush=True)


def pair_fresh(g: TopOfBook, k: TopOfBook, *, now_ms: int) -> bool:
    age = max(now_ms - int(g.ts_ms), now_ms - int(k.ts_ms))
    skew = abs(int(g.ts_ms) - int(k.ts_ms))
    return bool(0 <= age <= MAX_AGE_MS and skew <= MAX_SKEW_MS)


async def triggered_pair_stream(pair: Any, queue: asyncio.Queue) -> None:
    """Emit only atomic Gemini/Kalshi pairs that already satisfy the 50 ms gate."""
    kalshi = KalshiClient(base_url=KALSHI_PROD_REST)
    last_request_mono = 0.0
    backoff_until = 0.0
    reconnect = 0.25
    try:
        while True:
            try:
                async with websockets.connect(GEMINI_WS, open_timeout=5.0) as ws:
                    await ws.send(json.dumps({
                        "id": "xrp-triggered-maker",
                        "method": "SUBSCRIBE",
                        "params": [f"{pair.gemini_symbol}@bookTicker"],
                    }, separators=(",", ":")))
                    reconnect = 0.25
                    async for raw in ws:
                        receive_ms = int(time.time() * 1000)
                        payload = json.loads(raw)
                        if payload.get("status") is not None:
                            if int(payload.get("status", 0)) >= 400:
                                raise RuntimeError("Gemini public book subscription failed")
                            continue
                        if str(payload.get("s") or "").lower() != str(pair.gemini_symbol).lower():
                            continue
                        try:
                            g = parse_gemini_book_ticker(payload, received_ms=receive_ms)
                        except (KeyError, ValueError, TypeError):
                            continue
                        mono = time.monotonic()
                        if mono < backoff_until or (mono - last_request_mono) * 1000 < MIN_TRIGGER_MS:
                            continue
                        request_start_ms = int(time.time() * 1000)
                        # Fail before the network call if local scheduling already lost the skew gate.
                        if abs(request_start_ms - int(g.ts_ms)) > MAX_SKEW_MS:
                            continue
                        last_request_mono = mono
                        try:
                            response = await kalshi.get(f"/markets/{pair.kalshi_ticker}/orderbook", params={"depth": 1})
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            continue
                        if response.status_code == 429:
                            raw_retry = response.headers.get("retry-after", "").strip()
                            try:
                                retry = float(raw_retry) if raw_retry else 0.50
                            except ValueError:
                                retry = 0.50
                            backoff_until = time.monotonic() + max(0.25, retry)
                            continue
                        if response.status_code != 200:
                            continue
                        try:
                            k = rest_guard.exact_rest_top(response.json(), pair.kalshi_ticker, request_start_ms)
                        except Exception:
                            k = None
                        if k is None:
                            continue
                        now_ms = int(time.time() * 1000)
                        if not pair_fresh(g, k, now_ms=now_ms):
                            continue
                        item = (g, k)
                        try:
                            queue.put_nowait(item)
                        except asyncio.QueueFull:
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                            queue.put_nowait(item)
                raise RuntimeError("Gemini public book stream ended")
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(reconnect)
                reconnect = min(5.0, reconnect * 2)
    finally:
        await kalshi.close()


async def next_pair(queue: asyncio.Queue, *, timeout: float) -> tuple[TopOfBook, TopOfBook]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.01, deadline - time.monotonic())
        g, k = await asyncio.wait_for(queue.get(), timeout=remaining)
        if pair_fresh(g, k, now_ms=int(time.time() * 1000)):
            return g, k
    raise RuntimeError("no atomic <=50ms paired book")


async def discover_once() -> tuple[Any, Any, Mapping[str, Any], Mapping[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as http:
        pairs = await _discover_pairs(http, 30.0)
        future = sorted(
            (p for p in pairs if p.asset == ASSET and p.window_end.timestamp() > time.time()),
            key=lambda p: p.window_end,
        )
        if not future:
            raise RuntimeError("no current XRP 15m pair")
        pair = future[0]
        if pair.window_end.timestamp() - time.time() < MIN_SECONDS_TO_EXPIRY:
            raise RuntimeError("current XRP pair too close to expiry")
        fee_meta = await base.fetch_series_fee_metadata(http, base.ASSET_SERIES[ASSET])
        market = await _kalshi_market(http, pair.kalshi_ticker)
    geometry = await dominance.fetch_pair_geometry(pair, ASSET)
    return pair, fee_meta, market, geometry


def fillable_quote(*, g: TopOfBook, k: TopOfBook, market: Mapping[str, Any], fee_meta: Any) -> dict[str, Any] | None:
    quote = choose_quote(
        maker_best_bid=D(g.yes_bid),
        maker_best_ask=D(g.yes_ask),
        hedge_observed_price=D(k.no_ask),
        hedge_outcome="NO",
        kalshi_market=market,
        fee_meta=fee_meta,
        min_safe_first_fill=MIN_SAFE_FIRST_FILL,
        target=TARGET,
    )
    if quote is None:
        return None
    if D(quote["maker_quote"]) < D(g.yes_bid):
        return None
    return quote


async def run() -> dict[str, Any]:
    armed = canary.live_armed()
    trade = canary.trade_id()
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

    rows = await budget._trades()
    if any(str(row.get("trade_id") or "") == trade for row in rows):
        out["status"] = "DUPLICATE_CAMPAIGN_BLOCKED"
        emit("RESULT", **out)
        return out

    kalshi_exec = KalshiV2Executor()
    pair_task: asyncio.Task | None = None
    maker: canary.GeminiMakerSession | None = None
    reserved = False
    pair = None
    try:
        out["preflight"] = await canary._preflight(kalshi_exec, require_funds=armed)
        pair, fee_meta, market, geometry = await discover_once()
        out["geometry"] = geometry
        if not geometry.get("authorized") or str(geometry.get("dominance_safe_direction")) != "GEMINI_YES_KALSHI_NO":
            out["status"] = "DOMINANCE_NOT_AUTHORIZED_FOR_GEMINI_MAKER"
            emit("RESULT", **out)
            return out

        pair_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        pair_task = asyncio.create_task(triggered_pair_stream(pair, pair_queue))
        deadline = min(time.monotonic() + max(1.0, WAIT_SECONDS), time.monotonic() + max(1.0, pair.window_end.timestamp() - time.time() - MIN_SECONDS_TO_EXPIRY))
        ready: tuple[TopOfBook, TopOfBook, dict[str, Any]] | None = None
        examined = 0
        while time.monotonic() < deadline:
            try:
                g, k = await next_pair(pair_queue, timeout=min(2.0, max(0.05, deadline - time.monotonic())))
            except asyncio.TimeoutError:
                continue
            examined += 1
            quote = fillable_quote(g=g, k=k, market=market, fee_meta=fee_meta)
            if quote is not None:
                ready = (g, k, quote)
                break
        out["paired_books_examined"] = examined
        if ready is None:
            out["status"] = "NO_FILLABLE_PARTIAL_SAFE_AT_BID_WINDOW"
            emit("RESULT", **out)
            return out

        g, k, quote = ready
        out["quote"] = quote
        out["paired_book_skew_ms"] = abs(int(g.ts_ms) - int(k.ts_ms))
        if not armed:
            out["status"] = "READ_ONLY_READY_AT_BID"
            emit("RESULT", **out)
            return out

        candidate = {
            "maker_outcome": "YES",
            "hedge_outcome": "NO",
            "maker_price": str(quote["maker_quote"]),
            "hedge_price": str(quote["hedge_cushioned_price"]),
            "locked_profit": str(quote["q1_economics"]["locked_profit"]),
        }
        await budget.reserve(candidate=candidate, pair=pair, geometry=geometry, trade_id=trade)
        reserved = True

        maker = canary.GeminiMakerSession()
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
            out.update({"status": "MAKER_REJECTED_ZERO_FILL", "ack_status": int(ack.get("status", 0))})
            emit("RESULT", **out)
            return out

        row = None
        for _ in range(10):
            row = await canary._order_by_client(client_id, pair.gemini_symbol)
            if row is not None:
                break
            await asyncio.sleep(0.05)
        emit("MAKER_POSTED", trade_id=trade, maker_price=str(quote["maker_quote"]), order_recovered=bool(row), order_entry=True)

        fills = canary.MakerFill()
        first_fill_seen = False
        started = time.monotonic()
        cancel_reason = "REST_TIMEOUT"
        while time.monotonic() - started < REST_SECONDS:
            pair_get = asyncio.create_task(pair_queue.get())
            fill_get = asyncio.create_task(maker.events.get())
            done, pending = await asyncio.wait({pair_get, fill_get}, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                continue
            item = next(iter(done)).result()
            if isinstance(item, tuple) and len(item) == 2:
                g2, k2 = item
                if not pair_fresh(g2, k2, now_ms=int(time.time() * 1000)):
                    continue
                g, k = g2, k2
                tick = _hedge_tick(market, "NO", D(k.no_ask))
                hedge_limit = min(D("0.999"), D(k.no_ask) + tick)
                live_floor = economics(
                    maker_price=D(quote["maker_quote"]),
                    hedge_price=hedge_limit,
                    quantity=MIN_SAFE_FIRST_FILL,
                    fee_meta=fee_meta,
                )
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

        cancel = await canary._cancel_maker(maker, row=row)
        await asyncio.sleep(0.05)
        while not maker.events.empty():
            evt = maker.events.get_nowait()
            if isinstance(evt, Mapping) and str(evt.get("c") or "") == client_id:
                fills.add(evt)
        final_row = await canary._order_by_client(client_id, pair.gemini_symbol)
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

        if rest_filled > fills.quantity + D("0.000001"):
            flatten = await canary._flatten_gemini_yes(pair.gemini_symbol, quantity, trade)
            out.update({"status": "UNCAPTURED_CANCEL_RACE_FILL_FLATTENED_HARD_HALT", "flatten": flatten, "filled_quantity": str(quantity)})
            await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=1, metadata={"filled_quantity": str(quantity), "flatten": flatten})
            emit("RESULT", **out)
            return out

        try:
            g, k = await next_pair(pair_queue, timeout=2.0)
        except Exception:
            flatten = await canary._flatten_gemini_yes(pair.gemini_symbol, quantity, trade)
            out.update({"status": "POST_FILL_BOOK_UNAVAILABLE_GEMINI_FLATTENED_HARD_HALT", "flatten": flatten, "filled_quantity": str(quantity)})
            await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=1, metadata={"filled_quantity": str(quantity), "flatten": flatten})
            emit("RESULT", **out)
            return out

        if D(k.no_ask_size) < quantity:
            flatten = await canary._flatten_gemini_yes(pair.gemini_symbol, quantity, trade)
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
            "quantity": str(quantity),
            "maker_notional": str(maker_notional),
            "maker_fee_observed_or_conservative": str(maker_fees),
            "maker_execution_events": fills.executions,
            "missing_fee_events": fills.missing_fee_events,
            "hedge_limit": str(hedge_limit),
            "hedge_fee_conservative": str(hedge_fee),
            "locked_profit": str(locked),
            "locked_per_contract": str(locked_per_contract),
        }
        out["actual_fill_economics"] = actual_econ
        if locked_per_contract < TARGET:
            flatten = await canary._flatten_gemini_yes(pair.gemini_symbol, quantity, trade)
            out.update({"status": "FIRST_FILL_BELOW_3C_FLATTENED_HARD_HALT", "flatten": flatten, "filled_quantity": str(quantity)})
            await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=1, metadata={**actual_econ, "flatten": flatten})
            emit("RESULT", **out)
            return out

        hedge_payload = canary.kalshi_no_fok_payload(
            ticker=pair.kalshi_ticker,
            no_limit=hedge_limit,
            quantity=quantity,
            client_id=f"{trade}-kh",
        )
        hedge = await kalshi_exec.place(hedge_payload)
        hedge_filled = D(str(hedge.filled_quantity or "0"))
        if hedge.status != "FILLED" or hedge_filled != quantity:
            flatten = await canary._flatten_gemini_yes(pair.gemini_symbol, quantity, trade)
            out.update({"status": "KALSHI_HEDGE_FAILED_GEMINI_FLATTENED_HARD_HALT", "hedge": hedge.as_dict(), "flatten": flatten, "filled_quantity": str(quantity)})
            await budget.execution_result(pair=pair, trade_id=trade, status=out["status"], legs_filled=1, metadata={**actual_econ, "hedge": hedge.as_dict(), "flatten": flatten})
            emit("RESULT", **out)
            return out

        out.update({
            "status": "BOTH_FILLED_RECONCILED_NOT_SETTLED",
            "hedge": hedge.as_dict(),
            "filled_quantity": str(quantity),
            "counts_as_realized_arb": False,
            "realized": False,
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
        if pair_task is not None:
            pair_task.cancel()
            await asyncio.gather(pair_task, return_exceptions=True)
        if maker is not None:
            await maker.close()
        await kalshi_exec.close()


if __name__ == "__main__":
    asyncio.run(run())

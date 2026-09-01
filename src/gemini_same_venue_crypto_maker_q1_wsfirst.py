"""WS-first, sticky q=1 same-Gemini crypto ladder executor.

This is the production transport wrapper for the same-Gemini maker->FOK lane.
Public market state is Gemini 100ms depth WS. Private order lifecycle is
orders@account first. REST is used only as bounded authoritative recovery after a
missed/reconnected account stream or process restart.

Safety invariants:
- exact same-event/source/expiry HI ladder certification remains in base discovery;
- >=3c after-fee locked floor is unchanged;
- only one unresolved LIVE crypto reservation may exist;
- a quote is sticky while the hedge remains deep/fresh/safe; no timer-only cancel;
- ambiguous order state retains the reservation and hard-stops;
- prior unresolved same-venue orders are reconciled before any new live mutation.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
import time
from typing import Any, Mapping

import httpx
import websockets

from src import gemini_same_venue_crypto_budget as budget
from src import gemini_same_venue_crypto_ladder_scan as scan
from src import gemini_same_venue_crypto_maker_q1 as base
from src import gemini_same_venue_crypto_maker_q1_depth as depth
from src.gemini_kalshi_execution_shadow import (
    gemini_predictions_maker_fee,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto
from src.gemini_kalshi_probe_transport import GeminiTradingSession
from src.gemini_kalshi_xrp_maker_live_canary import GeminiMakerSession, MakerFill
from src.gemini_prediction_auth import private_rest_request

D = Decimal
ONE = D("1")
TARGET = D("0.03")
RECOVERY_ATTEMPTS = max(2, min(8, int(os.getenv("GEMINI_SAME_VENUE_RECOVERY_ATTEMPTS", "4"))))
RECOVERY_TIMEOUT = max(1.0, min(6.0, float(os.getenv("GEMINI_SAME_VENUE_RECOVERY_TIMEOUT_S", "2.5"))))
ACCOUNT_DISCOVERY_SECONDS = max(1.0, min(8.0, float(os.getenv("GEMINI_SAME_VENUE_ACCOUNT_DISCOVERY_S", "3"))))
PUBLIC_STALE_SECONDS = max(0.8, min(5.0, float(os.getenv("GEMINI_SAME_VENUE_PUBLIC_STALE_S", "2"))))
FLOW_SAMPLE_SECONDS = max(1.0, min(8.0, float(os.getenv("GEMINI_SAME_VENUE_FLOW_SAMPLE_S", "3"))))
FLOW_MAX_SYMBOLS = max(4, min(50, int(os.getenv("GEMINI_SAME_VENUE_FLOW_MAX_SYMBOLS", "30"))))
TERMINAL = {"FILLED", "CANCELED", "CANCELLED", "REJECTED"}
ACTIVE = {"NEW", "OPEN", "PARTIALLY_FILLED", "MODIFIED"}


def emit(stage: str, **fields: Any) -> None:
    base.emit(stage, transport="WS_FIRST_STICKY", **fields)


def _status(row: Mapping[str, Any] | None) -> str:
    if not row:
        return ""
    return str(row.get("status") or row.get("X") or "").upper()


def _order_id(row: Mapping[str, Any] | None) -> str | None:
    if not row:
        return None
    value = row.get("orderId") or row.get("order_id") or row.get("i") or row.get("id")
    return None if value in (None, "") else str(value)


def _filled(row: Mapping[str, Any] | None) -> D:
    if not row:
        return D("0")
    # REST rows carry filledQuantity. Account terminal events carry cumulative Z.
    raw = row.get("filledQuantity")
    if raw is None and _status(row) in TERMINAL:
        raw = row.get("Z")
    try:
        return max(D("0"), D(str(raw or "0")))
    except Exception:
        return D("0")


def _matching_client(row: Mapping[str, Any], client_id: str) -> bool:
    return str(row.get("clientOrderId") or row.get("c") or "") == client_id


async def _lookup_once(client_id: str, symbol: str, *, prefer_terminal: bool = False) -> tuple[dict[str, Any] | None, list[str]]:
    now_ms = int(time.time() * 1000)
    active = ("/v1/prediction-markets/orders/active", {"symbol": symbol, "limit": 100, "offset": 0}, "active")
    filled = ("/v1/prediction-markets/orders/history", {"status": "filled", "symbol": symbol, "limit": 100, "from": now_ms-8*3600*1000, "to": now_ms+1000}, "filled")
    cancelled = ("/v1/prediction-markets/orders/history", {"status": "cancelled", "symbol": symbol, "limit": 100, "from": now_ms-8*3600*1000, "to": now_ms+1000}, "cancelled")
    queries = [filled, cancelled, active] if prefer_terminal else [active, filled, cancelled]
    errors: list[str] = []
    found_active: dict[str, Any] | None = None
    for path, params, source in queries:
        try:
            response = await private_rest_request("POST", path, params=params, timeout=RECOVERY_TIMEOUT)
        except Exception as exc:
            errors.append(f"{source}:{type(exc).__name__}")
            continue
        if response.status_code != 200:
            errors.append(f"{source}:HTTP{response.status_code}")
            continue
        try:
            body = response.json()
        except Exception as exc:
            errors.append(f"{source}:JSON_{type(exc).__name__}")
            continue
        rows = body.get("orders", []) if isinstance(body, Mapping) else []
        for raw in rows:
            if not isinstance(raw, Mapping) or not _matching_client(raw, client_id):
                continue
            row = dict(raw)
            row["_recovery_source"] = source
            if _status(row) in TERMINAL:
                return row, errors
            found_active = row
    return found_active, errors


async def _bounded_lookup(client_id: str, symbol: str, *, prefer_terminal: bool = False) -> tuple[dict[str, Any] | None, list[str]]:
    all_errors: list[str] = []
    last: dict[str, Any] | None = None
    for attempt in range(RECOVERY_ATTEMPTS):
        row, errors = await _lookup_once(client_id, symbol, prefer_terminal=prefer_terminal)
        all_errors.extend(errors)
        if row is not None:
            last = row
            if not prefer_terminal or _status(row) in TERMINAL:
                return row, all_errors
        if attempt + 1 < RECOVERY_ATTEMPTS:
            await asyncio.sleep(min(0.8, 0.15 * (2 ** attempt)))
    return last, all_errors


async def _rest_cancel(order_id: str) -> bool:
    for attempt in range(RECOVERY_ATTEMPTS):
        try:
            response = await private_rest_request(
                "POST", "/v1/prediction-markets/order/cancel",
                params={"orderId": int(order_id) if order_id.isdigit() else order_id},
                timeout=RECOVERY_TIMEOUT,
            )
            if response.status_code == 200:
                return True
        except Exception:
            pass
        if attempt + 1 < RECOVERY_ATTEMPTS:
            await asyncio.sleep(min(0.8, 0.15 * (2 ** attempt)))
    return False


async def _wait_account_event(session: GeminiMakerSession, client_id: str, *, timeout: float,
                              fills: MakerFill | None = None) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.reader is not None and session.reader.done():
            return None
        try:
            event = await asyncio.wait_for(session.events.get(), timeout=min(0.25, max(0.02, deadline-time.monotonic())))
        except asyncio.TimeoutError:
            continue
        if not isinstance(event, Mapping) or not _matching_client(event, client_id):
            continue
        if fills is not None:
            fills.add(event)
        return dict(event)
    return None


async def _cancel_and_confirm(session: GeminiMakerSession, client_id: str, symbol: str,
                              order_id: str | None, fills: MakerFill) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    cancel: dict[str, Any] = {"ok": False, "method": None}
    reader_live = session.reader is not None and not session.reader.done()
    if reader_live:
        try:
            if order_id:
                ack = await session.request("order.cancel", {"orderId": int(order_id) if order_id.isdigit() else order_id}, timeout=3.0)
                cancel = {"ok": int(ack.get("status", 0)) == 200, "method": "WS_ORDER_CANCEL", "ack_status": int(ack.get("status", 0))}
            else:
                ack = await session.request("order.cancel_session", {}, timeout=3.0)
                cancel = {"ok": int(ack.get("status", 0)) == 200, "method": "WS_CANCEL_SESSION", "ack_status": int(ack.get("status", 0))}
        except Exception as exc:
            cancel = {"ok": False, "method": "WS_CANCEL_EXCEPTION", "error": type(exc).__name__}
    if cancel.get("ok"):
        event = await _wait_account_event(session, client_id, timeout=2.5, fills=fills)
        if event is not None and _status(event) in TERMINAL:
            return event, cancel
    # REST is recovery only. If WS is unhealthy or its terminal event was missed,
    # cancel exact order where possible and take bounded authoritative snapshots.
    if order_id and not cancel.get("ok"):
        cancel["rest_cancel_ok"] = await _rest_cancel(order_id)
    row, errors = await _bounded_lookup(client_id, symbol, prefer_terminal=True)
    cancel["recovery_errors"] = errors[-12:]
    if row is not None and _status(row) in TERMINAL:
        return row, cancel
    return row, cancel


def _recovery_candidate(row: Mapping[str, Any]) -> dict[str, Any] | None:
    market = str(row.get("market") or "")
    parts = market.split("|")
    if len(parts) != 2 or not all(parts):
        return None
    md = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    return {
        "maker_symbol": parts[0],
        "hedge_symbol": parts[1],
        "event_ticker": str(row.get("asset") or "CRYPTO"),
        "source": md.get("source"),
        "expiry": md.get("expiry"),
        "maker_outcome": md.get("maker_outcome"),
        "hedge_outcome": md.get("hedge_outcome"),
        "maker_quote": md.get("maker_quote"),
        "hedge_limit": md.get("hedge_limit_reserved"),
        "locked_profit": md.get("modeled_locked_profit"),
    }


async def reconcile_previous_unresolved() -> bool:
    """Resolve only an exact previous same-venue q1 maker before new order entry."""
    rows = await budget.trades()
    blocked = budget.unresolved_crypto(rows)
    if blocked is None:
        emit("STARTUP_RECOVERY_CLEAR", unresolved=False, order_entry=False, realized=False)
        return True
    strategy = str(blocked.get("strategy") or "").lower()
    trade_id = str(blocked.get("trade_id") or "")
    candidate = _recovery_candidate(blocked)
    if strategy != budget.STRATEGY or not trade_id.startswith("crypto-gem-ladder-") or candidate is None:
        emit("STARTUP_RECOVERY_BLOCKED_OTHER_CRYPTO", trade_id=trade_id, strategy=strategy, order_entry=False, realized=False)
        return False
    client_id = f"{trade_id}-mk"[:36]
    symbol = str(candidate["maker_symbol"])
    row, errors = await _bounded_lookup(client_id, symbol, prefer_terminal=True)
    if row is None:
        emit("STARTUP_RECOVERY_AMBIGUOUS", trade_id=trade_id, client_id=client_id, errors=errors[-12:], order_entry=False, realized=False)
        return False
    status = _status(row)
    qty = _filled(row)
    if qty > 0:
        emit("STARTUP_RECOVERY_FOUND_FILL", trade_id=trade_id, client_id=client_id, status=status, filled_quantity=str(qty), order_entry=False, realized=False)
        return False
    if status in ACTIVE:
        oid = _order_id(row)
        if not oid:
            emit("STARTUP_RECOVERY_ACTIVE_NO_ORDER_ID", trade_id=trade_id, client_id=client_id, order_entry=False, realized=False)
            return False
        cancelled = await _rest_cancel(oid)
        if not cancelled:
            emit("STARTUP_RECOVERY_CANCEL_FAILED", trade_id=trade_id, client_id=client_id, order_id=oid, order_entry=False, realized=False)
            return False
        row, errors2 = await _bounded_lookup(client_id, symbol, prefer_terminal=True)
        errors.extend(errors2)
        status = _status(row)
        qty = _filled(row)
    if row is not None and status in {"CANCELED", "CANCELLED", "REJECTED"} and qty == 0:
        await budget.release(candidate=candidate, trade_id=trade_id, reason="STARTUP_AUTHORITATIVE_ZERO_FILL_RECOVERY")
        emit("STARTUP_RECOVERY_RELEASED_ZERO_FILL", trade_id=trade_id, client_id=client_id, status=status,
             recovery_source=row.get("_recovery_source"), order_entry=False, realized=False)
        return True
    emit("STARTUP_RECOVERY_UNRESOLVED", trade_id=trade_id, client_id=client_id, status=status,
         filled_quantity=str(qty), errors=errors[-12:], order_entry=False, realized=False)
    return False


async def _sample_trade_flow(symbols: list[str]) -> dict[str, dict[str, D]]:
    stats = {s: {"buyer_maker_qty": D("0"), "buyer_taker_qty": D("0"), "total_qty": D("0"), "trades": D("0")} for s in symbols}
    if not symbols:
        return stats
    try:
        async with websockets.connect(base.GEMINI_WS, open_timeout=5.0) as ws:
            await ws.send(json.dumps({"id": "same-gemini-flow", "method": "SUBSCRIBE", "params": [f"{s}@trade" for s in symbols]}, separators=(",", ":")))
            deadline = time.monotonic() + FLOW_SAMPLE_SECONDS
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(0.5, max(0.05, deadline-time.monotonic())))
                except asyncio.TimeoutError:
                    continue
                payload = json.loads(raw)
                if payload.get("status") is not None:
                    if int(payload.get("status", 0)) >= 400:
                        break
                    continue
                symbol = str(payload.get("s") or "")
                if symbol not in stats or payload.get("q") is None or payload.get("m") is None:
                    continue
                try:
                    qty = D(str(payload.get("q")))
                except Exception:
                    continue
                if qty <= 0:
                    continue
                stats[symbol]["total_qty"] += qty
                stats[symbol]["trades"] += ONE
                if bool(payload.get("m")):
                    stats[symbol]["buyer_maker_qty"] += qty
                else:
                    stats[symbol]["buyer_taker_qty"] += qty
    except Exception as exc:
        emit("FLOW_SAMPLE_DEGRADED", error=type(exc).__name__, order_entry=False, realized=False)
    return stats


def _event_decimal(event: Mapping[str, Any], field: str) -> D:
    try:
        return max(D("0"), D(str(event.get(field) or "0")))
    except Exception:
        return D("0")


async def discover_flow_ranked() -> tuple[base.Candidate | None, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        contracts = base._contract_map(events)
        whole_symbols = {symbol for symbol, c in contracts.items() if base.whole_contract_only(c)}
        books, statuses = await scan._books(http, whole_symbols)
    actionable, joins, telemetry = scan.score(events, books)
    candidates = [c for row in actionable + joins if (c := base.candidate_from_row(row, contracts)) is not None]
    event_by_ticker = {str(e.get("ticker") or ""): e for e in events}

    def prekey(c: base.Candidate) -> tuple[Any, ...]:
        e = event_by_ticker.get(c.event_ticker, {})
        gap = (c.maker_ask-c.maker_quote) if c.maker_ask is not None else D("1")
        return (-_event_decimal(e, "volume"), -_event_decimal(e, "liquidity"), gap, c.seconds_to_expiry)

    candidates.sort(key=prekey)
    sample_symbols: list[str] = []
    for c in candidates:
        if c.maker_symbol not in sample_symbols:
            sample_symbols.append(c.maker_symbol)
        if len(sample_symbols) >= FLOW_MAX_SYMBOLS:
            break
    flow = await _sample_trade_flow(sample_symbols)

    def flow_fields(c: base.Candidate) -> tuple[D, D, D, D, D, int]:
        s = flow.get(c.maker_symbol, {})
        desired = D(str(s.get("buyer_maker_qty") or "0")) if c.maker_outcome == "YES" else D(str(s.get("buyer_taker_qty") or "0"))
        total = D(str(s.get("total_qty") or "0"))
        e = event_by_ticker.get(c.event_ticker, {})
        vol = _event_decimal(e, "volume")
        liq = _event_decimal(e, "liquidity")
        gap = (c.maker_ask-c.maker_quote) if c.maker_ask is not None else D("1")
        queue = 2 if c.action == "IMPROVE_TO_SAFE_FRONTIER" else (1 if c.action == "JOIN_SAFE_BID" else 0)
        return desired, total, vol, liq, gap, queue

    def rank(c: base.Candidate) -> tuple[Any, ...]:
        desired, total, vol, liq, gap, queue = flow_fields(c)
        return (-desired, -total, -vol, -liq, gap, -queue, c.seconds_to_expiry, -c.hedge_depth)

    candidates.sort(key=rank)
    top = []
    for c in candidates[:8]:
        desired, total, vol, liq, gap, queue = flow_fields(c)
        top.append({**c.as_dict(), "maker_side_aggressor_qty_sample": str(desired), "total_trade_qty_sample": str(total),
                    "event_volume_usd": str(vol), "event_liquidity_usd": str(liq), "gap_to_ask": str(gap), "queue_priority_class": queue})
    diag = {
        **telemetry,
        "whole_contract_symbols": len(whole_symbols),
        "whole_books_ok": sum(1 for s in whole_symbols if statuses.get(s) == 200),
        "robust_whole_contract_candidates": len(candidates),
        "flow_sample_seconds": FLOW_SAMPLE_SECONDS,
        "flow_symbols_sampled": len(sample_symbols),
        "selection_policy": "maker-side aggressor flow > total live flow > event volume > liquidity > proximity > queue class > expiry > hedge depth",
        "top": top,
        "read_only": not base.live_armed(),
        "order_entry": False,
        "realized": False,
    }
    return (candidates[0] if candidates else None), diag


async def _hedge_confirmed_fill(candidate: base.Candidate, cdict: dict[str, Any], trade_id: str,
                                quote: D, queue: asyncio.Queue, maker_top: Any, hedge_top: Any,
                                fills: MakerFill, filled: D) -> dict[str, Any]:
    if filled != ONE:
        flatten = await base._flatten(candidate.maker_symbol, candidate.maker_outcome, trade_id)
        status = "NON_WHOLE_MAKER_FILL_HARD_HALT"
        await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=1,
                                      metadata={"filled_quantity": str(filled), "flatten": flatten})
        return {"status": status, "filled_quantity": str(filled), "flatten": flatten, "order_entry": True}

    maker_top, hedge_top = await base._fresh_pair(queue, maker_top=maker_top, hedge_top=hedge_top, timeout=2.0)
    _, _, live_hedge_ask, live_hedge_depth = base._outcome_top(hedge_top, candidate.hedge_outcome)
    if live_hedge_depth < ONE:
        flatten = await base._flatten(candidate.maker_symbol, candidate.maker_outcome, trade_id)
        status = "HEDGE_DEPTH_LOST_FLATTENED_HARD_HALT"
        await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=1,
                                      metadata={"live_hedge_ask": str(live_hedge_ask), "flatten": flatten})
        return {"status": status, "flatten": flatten, "order_entry": True}

    maker_fee = fills.fees if fills.executions > 0 else gemini_predictions_maker_fee(quote, ONE)
    hedge_fee = gemini_predictions_taker_fee(live_hedge_ask, ONE)
    prehedge_locked = ONE - quote - live_hedge_ask - maker_fee - hedge_fee
    if prehedge_locked < TARGET:
        flatten = await base._flatten(candidate.maker_symbol, candidate.maker_outcome, trade_id)
        status = "POST_FILL_EDGE_BELOW_3C_FLATTENED_HARD_HALT"
        await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=1,
                                      metadata={"prehedge_locked": str(prehedge_locked), "flatten": flatten})
        return {"status": status, "prehedge_locked": str(prehedge_locked), "flatten": flatten, "order_entry": True}

    hedge_session = GeminiTradingSession()
    try:
        await hedge_session.connect()
        hedge = await hedge_session.place_fok({
            "symbol": candidate.hedge_symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
            "price": str(live_hedge_ask), "quantity": "1", "eventOutcome": candidate.hedge_outcome,
            "clientOrderId": f"{trade_id}-hg"[:36],
        }, terminal_timeout=3.0)
    finally:
        await hedge_session.close()
    if hedge.status != "FILLED" or D(str(hedge.filled_quantity or "0")) != ONE:
        flatten = await base._flatten(candidate.maker_symbol, candidate.maker_outcome, trade_id)
        status = "SIBLING_FOK_FAILED_MAKER_FLATTENED_HARD_HALT"
        await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=1,
                                      metadata={"hedge": hedge.as_dict(), "flatten": flatten})
        return {"status": status, "hedge": hedge.as_dict(), "flatten": flatten, "order_entry": True}

    hedge_price = D(str(hedge.average_fill_price or live_hedge_ask))
    hedge_fee_actual = D(str(hedge.fee_paid or gemini_predictions_taker_fee(hedge_price, ONE)))
    maker_notional = fills.notional if fills.notional > 0 else quote
    maker_fee_actual = fills.fees if fills.executions > 0 else gemini_predictions_maker_fee(quote, ONE)
    locked = ONE - maker_notional - maker_fee_actual - hedge_price - hedge_fee_actual
    status = "BOTH_FILLED_RECONCILED_NOT_SETTLED"
    await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=2, metadata={
        "maker_fill_price": str(maker_notional), "maker_fee": str(maker_fee_actual),
        "hedge_fill_price": str(hedge_price), "hedge_fee": str(hedge_fee_actual),
        "locked_profit_before_settlement": str(locked), "counts_as_realized_arb": False, "settlement_pending": True,
        "private_state_transport": "orders@account",
    })
    return {"status": status, "order_entry": True, "filled_quantity": "1",
            "locked_profit_before_settlement": str(locked), "counts_as_realized_arb": False, "realized": False}


async def attempt_wsfirst(candidate: base.Candidate, attempt_no: int) -> dict[str, Any]:
    cid = base._clean_id(os.getenv(base.CANARY_ID_ENV, ""))
    trade_id = f"crypto-gem-ladder-{cid}-{attempt_no:03d}"[:64]
    cdict = candidate.as_dict()
    await budget.reserve(candidate=cdict, trade_id=trade_id)
    reserved = True
    maker = GeminiMakerSession()
    public_tasks: list[asyncio.Task] = []
    client_id = ""
    order_id: str | None = None
    order_submitted = False
    fills = MakerFill()
    quote = candidate.maker_quote
    maker_top = hedge_top = None
    try:
        await maker.connect()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        public_tasks = [asyncio.create_task(base._book_stream(candidate.maker_symbol, "maker", queue)),
                        asyncio.create_task(base._book_stream(candidate.hedge_symbol, "hedge", queue))]
        maker_top, hedge_top = await base._fresh_pair(queue)
        ok, live = base._live_floor(candidate, maker_top, hedge_top)
        if not ok:
            await budget.release(candidate=cdict, trade_id=trade_id, reason="PRE_POST_FLOOR_LOST")
            reserved = False
            return {"status": "PRE_POST_FLOOR_LOST", "order_entry": False, "live": live}
        maker_bid, _, maker_ask, _ = base._outcome_top(maker_top, candidate.maker_outcome)
        _, _, hedge_ask, hedge_depth = base._outcome_top(hedge_top, candidate.hedge_outcome)
        robust = base.robust_frontier(maker_ask=maker_ask, observed_hedge_ask=hedge_ask,
                                      maker_tick=candidate.maker_tick, hedge_tick=candidate.hedge_tick)
        if robust is None:
            await budget.release(candidate=cdict, trade_id=trade_id, reason="NO_FRESH_ROBUST_FRONTIER")
            reserved = False
            return {"status": "NO_FRESH_ROBUST_FRONTIER", "order_entry": False}
        quote, hedge_limit, modeled = robust
        action = scan.maker_action(existing_bid=maker_bid, safe_quote=quote, tick=candidate.maker_tick)
        if action not in {"SEED_FIRST_BID", "IMPROVE_TO_SAFE_FRONTIER", "JOIN_SAFE_BID"} or hedge_depth < ONE:
            await budget.release(candidate=cdict, trade_id=trade_id, reason="FRESH_TOP_NOT_MAKER_ACTIONABLE")
            reserved = False
            return {"status": "FRESH_TOP_NOT_MAKER_ACTIONABLE", "order_entry": False, "action": action}
        cdict.update({"maker_quote": str(quote), "hedge_limit": str(hedge_limit), "locked_profit": str(modeled)})
        client_id = f"{trade_id}-mk"[:36]
        ack = await maker.request("order.place", {
            "symbol": candidate.maker_symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "MOC",
            "price": str(quote), "quantity": "1", "eventOutcome": candidate.maker_outcome, "clientOrderId": client_id,
        }, timeout=4.0)
        order_submitted = True
        if int(ack.get("status", 0)) != 200:
            await budget.release(candidate=cdict, trade_id=trade_id, reason="MAKER_REJECTED_ZERO_FILL")
            reserved = False
            return {"status": "MAKER_REJECTED_ZERO_FILL", "order_entry": True, "ack_status": int(ack.get("status", 0))}
        emit("MAKER_POSTED", trade_id=trade_id, client_id=client_id, maker_symbol=candidate.maker_symbol,
             maker_outcome=candidate.maker_outcome, maker_price=str(quote), hedge_symbol=candidate.hedge_symbol,
             hedge_outcome=candidate.hedge_outcome, hedge_limit_reserved=str(hedge_limit), locked_profit=str(modeled), order_entry=True)

        first_event = await _wait_account_event(maker, client_id, timeout=ACCOUNT_DISCOVERY_SECONDS, fills=fills)
        state_row: dict[str, Any] | None = first_event
        if state_row is None:
            state_row, errors = await _bounded_lookup(client_id, candidate.maker_symbol)
            emit("ACCOUNT_WS_DISCOVERY_RECOVERY", trade_id=trade_id, found=state_row is not None,
                 errors=errors[-12:], order_entry=False, realized=False)
        order_id = _order_id(state_row)
        observed_filled = max(fills.quantity, _filled(state_row))
        if state_row is None:
            # The post ack succeeded but we cannot identify the order. Cancel the
            # session and recover authoritatively; never release on ambiguity.
            terminal, cancel = await _cancel_and_confirm(maker, client_id, candidate.maker_symbol, order_id, fills)
            observed_filled = max(fills.quantity, _filled(terminal))
            if terminal is not None and _status(terminal) in {"CANCELED", "CANCELLED", "REJECTED"} and observed_filled == 0:
                await budget.release(candidate=cdict, trade_id=trade_id, reason="POST_ACK_STATE_RECOVERED_ZERO_FILL")
                reserved = False
                return {"status": "POST_ACK_STATE_RECOVERED_ZERO_FILL", "order_entry": True, "cancel": cancel}
            status = "ORDER_STATE_AMBIGUOUS_AFTER_POST_HARD_HALT"
            await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=0,
                                          metadata={"client_id": client_id, "cancel": cancel})
            return {"status": status, "order_entry": True, "client_id": client_id}
        if observed_filled > 0:
            return await _hedge_confirmed_fill(candidate, cdict, trade_id, quote, queue, maker_top, hedge_top, fills, observed_filled)
        if _status(state_row) in {"CANCELED", "CANCELLED", "REJECTED"}:
            await budget.release(candidate=cdict, trade_id=trade_id, reason="EXCHANGE_TERMINAL_ZERO_FILL")
            reserved = False
            return {"status": "MAKER_CANCELED_ZERO_FILL", "order_entry": True, "cancel_reason": "EXCHANGE_TERMINAL"}

        # Sticky maker: there is deliberately no age-based timeout. Periodic 100ms
        # public depth snapshots prove hedge freshness; account WS proves order state.
        started = time.monotonic()
        last_public = started
        cancel_reason = ""
        terminal_seen: dict[str, Any] | None = None
        while True:
            if maker.reader is None or maker.reader.done():
                cancel_reason = "ACCOUNT_WS_LOST"
                break
            if time.monotonic() - started >= max(0.0, candidate.seconds_to_expiry-base.MIN_SECONDS_TO_EXPIRY):
                cancel_reason = "EXPIRY_SAFETY_WINDOW"
                break
            public_get = asyncio.create_task(queue.get())
            account_get = asyncio.create_task(maker.events.get())
            done, pending = await asyncio.wait({public_get, account_get}, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                if time.monotonic() - last_public > PUBLIC_STALE_SECONDS:
                    cancel_reason = "PUBLIC_WS_STALE"
                    break
                continue
            should_break = False
            for task in done:
                item = task.result()
                if isinstance(item, tuple) and len(item) == 2:
                    kind, top = item
                    last_public = time.monotonic()
                    if kind == "maker": maker_top = top
                    elif kind == "hedge": hedge_top = top
                    if maker_top is not None and hedge_top is not None:
                        now_ms = int(time.time()*1000)
                        if max(now_ms-int(maker_top.ts_ms), now_ms-int(hedge_top.ts_ms)) > base.MAX_BOOK_AGE_MS:
                            cancel_reason = "BOOK_FRESHNESS_LOST"; should_break = True; break
                        ok, _ = base._live_floor(candidate, maker_top, hedge_top)
                        _, _, h_ask, h_depth = base._outcome_top(hedge_top, candidate.hedge_outcome)
                        cushion = base.locked_profit(quote, min(D("0.99"), h_ask+candidate.hedge_tick))
                        if not ok or h_depth < ONE or cushion < TARGET:
                            cancel_reason = "HEDGE_FLOOR_DROPPED"; should_break = True; break
                elif isinstance(item, Mapping) and _matching_client(item, client_id):
                    fills.add(item)
                    order_id = _order_id(item) or order_id
                    if _filled(item) > fills.quantity and _status(item) in TERMINAL:
                        # Terminal Z is cumulative; MakerFill intentionally only adds
                        # execution events, so preserve the larger authoritative qty.
                        pass
                    observed_filled = max(fills.quantity, _filled(item))
                    if observed_filled > 0:
                        cancel_reason = "FIRST_FILL_FREEZE"; terminal_seen = dict(item); should_break = True; break
                    if _status(item) in TERMINAL:
                        cancel_reason = "EXCHANGE_TERMINAL"; terminal_seen = dict(item); should_break = True; break
            if should_break:
                break

        observed_filled = max(fills.quantity, _filled(terminal_seen))
        if observed_filled > 0 and terminal_seen is not None and _status(terminal_seen) == "FILLED":
            return await _hedge_confirmed_fill(candidate, cdict, trade_id, quote, queue, maker_top, hedge_top, fills, observed_filled)
        if terminal_seen is not None and _status(terminal_seen) in {"CANCELED", "CANCELLED", "REJECTED"} and observed_filled == 0:
            await budget.release(candidate=cdict, trade_id=trade_id, reason=cancel_reason)
            reserved = False
            return {"status": "MAKER_CANCELED_ZERO_FILL", "order_entry": True, "cancel_reason": cancel_reason}

        terminal, cancel = await _cancel_and_confirm(maker, client_id, candidate.maker_symbol, order_id, fills)
        observed_filled = max(fills.quantity, _filled(terminal))
        if terminal is None or _status(terminal) not in TERMINAL:
            status = "ORDER_STATE_AMBIGUOUS_AFTER_CANCEL_HARD_HALT"
            await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=0,
                                          metadata={"client_id": client_id, "cancel_reason": cancel_reason, "cancel": cancel})
            return {"status": status, "order_entry": True, "cancel_reason": cancel_reason, "cancel": cancel}
        if observed_filled <= 0:
            await budget.release(candidate=cdict, trade_id=trade_id, reason=cancel_reason)
            reserved = False
            return {"status": "MAKER_CANCELED_ZERO_FILL", "order_entry": True, "cancel_reason": cancel_reason}
        return await _hedge_confirmed_fill(candidate, cdict, trade_id, quote, queue, maker_top, hedge_top, fills, observed_filled)
    except Exception as exc:
        # Any exception after ack gets a bounded cleanup attempt. Capital is released
        # only if terminal zero-fill is proven; otherwise the reservation remains.
        cleanup: dict[str, Any] | None = None
        if order_submitted and client_id:
            try:
                terminal, cleanup = await _cancel_and_confirm(maker, client_id, candidate.maker_symbol, order_id, fills)
                if terminal is not None and _status(terminal) in {"CANCELED", "CANCELLED", "REJECTED"} and max(fills.quantity, _filled(terminal)) == 0:
                    await budget.release(candidate=cdict, trade_id=trade_id, reason=f"EXCEPTION_RECOVERED_ZERO_FILL_{type(exc).__name__}")
                    reserved = False
                    return {"status": "EXCEPTION_RECOVERED_ZERO_FILL", "error": type(exc).__name__, "order_entry": True, "cancel": cleanup}
            except Exception:
                pass
        status = "HARD_HALT_EXCEPTION"
        if reserved:
            try:
                await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=0,
                                              metadata={"error_type": type(exc).__name__, "error": str(exc)[:180], "client_id": client_id, "cleanup": cleanup})
            except Exception:
                pass
        return {"status": status, "error": f"{type(exc).__name__}:{str(exc)[:220]}", "order_entry": bool(order_submitted)}
    finally:
        for task in public_tasks:
            task.cancel()
        await asyncio.gather(*public_tasks, return_exceptions=True)
        await maker.close()


async def run() -> dict[str, Any]:
    # Install the periodic 100ms depth transport directly; do not call depth.run(),
    # because this wrapper owns startup recovery and the attempt lifecycle.
    base._book_stream = depth._depth_book_stream
    base.discover = discover_flow_ranked
    base._attempt = attempt_wsfirst
    if base.live_armed():
        try:
            clear = await reconcile_previous_unresolved()
        except Exception as exc:
            emit("STARTUP_RECOVERY_EXCEPTION", error=f"{type(exc).__name__}:{str(exc)[:180]}", order_entry=False, realized=False)
            return {"status": "STARTUP_RECOVERY_EXCEPTION_HARD_HALT", "order_entry": False, "realized": False}
        if not clear:
            return {"status": "STARTUP_UNRESOLVED_CRYPTO_HARD_HALT", "order_entry": False, "realized": False}
    return await base.run()


if __name__ == "__main__":
    asyncio.run(run())

"""Bounded q=1 live canary for one rule-certified same-Gemini combo cover.

DISARMED by default. Live mutation requires both:
  ENABLE_GEMINI_COMBO_LIVE_Q1=YES
  GEMINI_COMBO_ARM_CAMPAIGN == GEMINI_COMBO_CAMPAIGN_ID != ""

Execution sequence is deliberately conservative:
  1) Fresh terms/balance/book/idempotency checks.
  2) FOK BUY one exact complement hedge on each component (max ~6c cost).
  3) Only after both hedge fills, place one maker-only q=1 combo BUY YES.
  4) Wait a bounded TTL. If combo does not fill, cancel and flatten hedges.
  5) Any ambiguous lifecycle hard-stops; no repeat on the same campaign IDs.

A three-leg fill is FILLED_NOT_SETTLED until Gemini settles every component.
No result in this module is classified REALIZED.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal, ROUND_CEILING
import json
import os
import time
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_probe_transport import GeminiTradingSession, ExecutionResult
from src.gemini_prediction_auth import private_rest_request

D = Decimal
ONE = D("1")
BASE = "https://api.gemini.com"

COMBO = "GEMI-CMB-260823-8EC8FBCF7988E34C-CMB"
HEDGES = [
    {"symbol": "GEMI-MLS-2608222330-TOR-MIA-REGT-O0", "outcome": "NO", "max_price": D("0.02")},
    {"symbol": "GEMI-MLS-2608230030-CLB-NSH-REGS-CLB2", "outcome": "YES", "max_price": D("0.02")},
]
COMBO_MAKER_PRICE = D("0.90")
TARGET_LOCK = D("0.03")
QTY = D("1")
DEFAULT_TTL_SECONDS = 120


def _fee(rate: D, price: D, qty: D = QTY) -> D:
    raw = rate * qty * price * (ONE - price)
    return (raw * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def _d(value: Any) -> D | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _level(row: Any) -> tuple[D, D] | None:
    if not isinstance(row, Mapping):
        return None
    p = _d(row.get("price"))
    q = _d(row.get("amount") if row.get("amount") is not None else row.get("quantity"))
    if p is None or q is None or not (D("0") < p < ONE) or q <= 0:
        return None
    return p, q


def _outcome_ask(book: Any, outcome: str) -> tuple[D, D] | None:
    if not isinstance(book, Mapping):
        return None
    asks = [z for z in (_level(x) for x in (book.get("asks") or [])) if z]
    bids = [z for z in (_level(x) for x in (book.get("bids") or [])) if z]
    if outcome == "YES":
        return min(asks, key=lambda z: z[0]) if asks else None
    if outcome == "NO":
        if not bids:
            return None
        bp, bq = max(bids, key=lambda z: z[0])
        return ONE - bp, bq
    return None


def _order_id(row: Any) -> str | None:
    if not isinstance(row, Mapping):
        return None
    for key in ("orderId", "order_id", "id", "i"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _row_filled_qty(row: Any) -> D:
    if not isinstance(row, Mapping):
        return D("0")
    for key in ("filledQuantity", "filled_quantity", "executedQuantity", "executed_quantity"):
        v = _d(row.get(key))
        if v is not None:
            return v
    return D("0")


def _result_dict(result: ExecutionResult | None) -> dict[str, Any] | None:
    return result.as_dict() if result is not None else None


async def _private_orders(path: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    response = await private_rest_request("POST", path, params=body, timeout=5.0)
    if response.status_code != 200:
        return []
    try:
        parsed = response.json()
    except Exception:
        return []
    rows = parsed.get("orders") if isinstance(parsed, Mapping) else []
    return [x for x in (rows or []) if isinstance(x, dict)]


async def _find_campaign_order(client_id: str, symbol: str) -> tuple[str, dict[str, Any]] | None:
    """Find active or recent terminal order by exact client ID."""
    active = await _private_orders(
        "/v1/prediction-markets/orders/active",
        {"symbol": symbol, "limit": 100, "offset": 0},
    )
    for row in active:
        if str(row.get("clientOrderId") or "") == client_id:
            return "active", row
    now_ms = int(time.time() * 1000)
    for status in ("filled", "cancelled"):
        rows = await _private_orders(
            "/v1/prediction-markets/orders/history",
            {"status": status, "symbol": symbol, "limit": 100, "from": now_ms - 86400000, "to": now_ms + 1000},
        )
        for row in rows:
            if str(row.get("clientOrderId") or "") == client_id:
                return status, row
    return None


async def _fresh_preflight(ids: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {"pass": False, "order_entry": False}
    terms = await private_rest_request("GET", "/v1/prediction-markets/terms/status", timeout=5.0)
    out["terms_http"] = terms.status_code
    try:
        tb = terms.json()
    except Exception:
        tb = {}
    out["terms_accepted"] = bool(tb.get("hasAcceptedLatest")) if isinstance(tb, Mapping) else False

    balances = await private_rest_request("POST", "/v1/balances", timeout=5.0)
    out["balance_http"] = balances.status_code
    usd = D("0")
    try:
        rows = balances.json()
    except Exception:
        rows = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping) and str(row.get("currency") or "").upper() == "USD":
                usd = _d(row.get("available")) or D("0")
                break
    out["usd_available"] = str(usd)

    prior = {}
    specs = [(ids["h1"], HEDGES[0]["symbol"]), (ids["h2"], HEDGES[1]["symbol"]), (ids["combo"], COMBO)]
    for cid, sym in specs:
        found = await _find_campaign_order(cid, sym)
        if found:
            prior[cid] = {"source": found[0], "status": str(found[1].get("status") or ""), "filled_quantity": str(_row_filled_qty(found[1])), "order_id": _order_id(found[1])}
    out["prior_campaign_orders"] = prior

    symbols = [COMBO, HEDGES[0]["symbol"], HEDGES[1]["symbol"]]
    books: dict[str, Any] = {}
    statuses: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=8.0) as h:
        async def one(symbol: str) -> None:
            r = await h.get(f"{BASE}/v1/book/{symbol}", params={"limit_bids": 20, "limit_asks": 20})
            statuses[symbol] = r.status_code
            try:
                books[symbol] = r.json()
            except Exception:
                books[symbol] = {}
        await asyncio.gather(*(one(s) for s in symbols))

    hedge_rows = []
    hedge_all_in = D("0")
    hedge_ok = True
    for spec in HEDGES:
        top = _outcome_ask(books.get(spec["symbol"]), spec["outcome"])
        row = {"symbol": spec["symbol"], "outcome": spec["outcome"], "http": statuses.get(spec["symbol"])}
        if top is None:
            row["available"] = False
            hedge_ok = False
        else:
            p, depth = top
            fee = _fee(D("0.07"), p)
            row.update({"available": True, "ask": str(p), "depth": str(depth), "fee": str(fee), "all_in": str(p + fee)})
            hedge_all_in += p + fee
            if p > spec["max_price"] or depth < QTY:
                hedge_ok = False
        hedge_rows.append(row)

    combo_book = books.get(COMBO) if isinstance(books.get(COMBO), Mapping) else {}
    combo_asks = [z for z in (_level(x) for x in (combo_book.get("asks") or [])) if z]
    best_combo_ask = min((p for p, _ in combo_asks), default=None)
    maker_fee = _fee(D("0.0175"), COMBO_MAKER_PRICE)
    locked = ONE - (hedge_all_in + COMBO_MAKER_PRICE + maker_fee)
    out.update({
        "hedges": hedge_rows,
        "hedge_all_in": str(hedge_all_in),
        "combo_book_http": statuses.get(COMBO),
        "combo_best_ask": str(best_combo_ask) if best_combo_ask is not None else None,
        "combo_maker_price": str(COMBO_MAKER_PRICE),
        "combo_maker_fee_conservative": str(maker_fee),
        "modeled_locked_if_filled": str(locked),
        "rule_certification": "COMBO_RULE_13_165_PRODUCT_PLUS_EXACT_COMPONENT_COMPLEMENTS",
    })
    out["pass"] = bool(
        out["terms_accepted"]
        and usd >= D("1")
        and not prior
        and hedge_ok
        and locked >= TARGET_LOCK
    )
    return out


async def _flatten(session: GeminiTradingSession, original: dict[str, Any]) -> ExecutionResult:
    return await session.emergency_flatten_market(original, timeout=3.0)


async def _maker_with_ttl(
    session: GeminiTradingSession,
    params: dict[str, Any],
    *,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Place one MOC quote, await terminal state, then cancel on TTL."""
    client_id = str(params["clientOrderId"])
    loop = asyncio.get_running_loop()
    terminal = loop.create_future()
    # Isolated canary intentionally uses the session's existing event demux maps
    # rather than modifying shared transport code used by unrelated strategies.
    session._terminal[client_id] = terminal
    session._fill_qty[client_id] = D("0")
    session._fill_notional[client_id] = D("0")
    session._fees[client_id] = D("0")
    sent_ns = time.perf_counter_ns()
    ack_ns: int | None = None
    try:
        try:
            response = await session.request("order.place", params, timeout=4.0)
            ack_ns = time.perf_counter_ns()
        except Exception as exc:
            row = await session._reconcile_rest(params)
            if row is None:
                return {"status": "UNKNOWN", "reason": f"AMBIGUOUS_MAKER_SUBMISSION:{type(exc).__name__}", "filled_quantity": "0", "order_id": None}
            source = "REST_RECONCILE_AFTER_ACK_ERROR"
        else:
            status_code = int(response.get("status", 0))
            if status_code != 200:
                error = response.get("error") if isinstance(response.get("error"), Mapping) else {}
                return {"status": "REJECTED", "reason": str(error.get("msg") or error.get("code") or "request_rejected"), "filled_quantity": "0", "order_id": None, "raw_status": status_code}
            row = await session._reconcile_rest(params)
            source = "ACK_200"

        # If an account event already completed the order, consume it immediately.
        if terminal.done():
            event = terminal.result()
            result = session._event_result(client_id=client_id, event=event, sent_ns=sent_ns, ack_ns=ack_ns, raw_status=200, reconciled=False)
            return {**result.as_dict(), "order_id": _order_id(row), "source": source}

        try:
            event = await asyncio.wait_for(asyncio.shield(terminal), timeout=float(ttl_seconds))
            result = session._event_result(client_id=client_id, event=event, sent_ns=sent_ns, ack_ns=ack_ns, raw_status=200, reconciled=False)
            return {**result.as_dict(), "order_id": _order_id(row), "source": source}
        except asyncio.TimeoutError:
            pass

        row = await session._reconcile_rest(params)
        if row is None:
            # Last-resort session-scoped cancel avoids leaving this process's quote
            # resting when the order ID cannot be recovered.
            try:
                await session.request("order.cancel_session", {}, timeout=3.0)
            except Exception:
                return {"status": "UNKNOWN", "reason": "TTL_ORDER_NOT_RECOVERABLE_CANCEL_SESSION_FAILED", "filled_quantity": str(session._fill_qty.get(client_id, D("0"))), "order_id": None}
        else:
            oid = _order_id(row)
            status = str(row.get("status") or "").upper()
            if status == "FILLED" or _row_filled_qty(row) >= QTY:
                return {"status": "FILLED", "reason": "REST_RECONCILED_AT_TTL", "filled_quantity": str(_row_filled_qty(row)), "average_fill_price": str(row.get("avgExecutionPrice")) if row.get("avgExecutionPrice") is not None else None, "fee_paid": None, "order_id": oid, "source": "REST"}
            if oid:
                try:
                    await session.request("order.cancel", {"orderId": str(oid)}, timeout=3.0)
                except Exception:
                    cancel = await private_rest_request("POST", "/v1/prediction-markets/order/cancel", params={"orderId": int(oid) if str(oid).isdigit() else oid}, timeout=5.0)
                    if cancel.status_code != 200:
                        return {"status": "UNKNOWN", "reason": f"TTL_CANCEL_FAILED_HTTP_{cancel.status_code}", "filled_quantity": str(_row_filled_qty(row)), "order_id": oid}
            else:
                try:
                    await session.request("order.cancel_session", {}, timeout=3.0)
                except Exception:
                    return {"status": "UNKNOWN", "reason": "TTL_ACTIVE_ORDER_ID_MISSING_CANCEL_SESSION_FAILED", "filled_quantity": str(_row_filled_qty(row)), "order_id": None}

        try:
            event = await asyncio.wait_for(asyncio.shield(terminal), timeout=4.0)
            result = session._event_result(client_id=client_id, event=event, sent_ns=sent_ns, ack_ns=ack_ns, raw_status=200, reconciled=True)
            return {**result.as_dict(), "order_id": _order_id(row), "source": "TTL_CANCEL"}
        except Exception:
            final = await session._reconcile_rest(params)
            if final is None:
                return {"status": "UNKNOWN", "reason": "POST_CANCEL_RECONCILE_FAILED", "filled_quantity": str(session._fill_qty.get(client_id, D("0"))), "order_id": _order_id(row)}
            return {"status": str(final.get("status") or "UNKNOWN").upper(), "reason": "POST_CANCEL_REST", "filled_quantity": str(_row_filled_qty(final)), "average_fill_price": str(final.get("avgExecutionPrice")) if final.get("avgExecutionPrice") is not None else None, "fee_paid": None, "order_id": _order_id(final) or _order_id(row), "source": "REST"}
    finally:
        session._terminal.pop(client_id, None)
        session._fill_qty.pop(client_id, None)
        session._fill_notional.pop(client_id, None)
        session._fees.pop(client_id, None)


async def run_once() -> dict[str, Any]:
    campaign = os.getenv("GEMINI_COMBO_CAMPAIGN_ID", "").strip()
    armed_campaign = os.getenv("GEMINI_COMBO_ARM_CAMPAIGN", "").strip()
    enable = os.getenv("ENABLE_GEMINI_COMBO_LIVE_Q1", "").strip().upper() == "YES"
    ttl = max(30, min(300, int(os.getenv("GEMINI_COMBO_MAKER_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))))
    ids = {
        "h1": f"{campaign}-h1"[:36],
        "h2": f"{campaign}-h2"[:36],
        "combo": f"{campaign}-c"[:36],
    }
    out: dict[str, Any] = {
        "status": "GEMINI_EXTREME_COMBO_LIVE_CANARY",
        "campaign": campaign,
        "armed": bool(enable and campaign and armed_campaign == campaign),
        "quantity": "1",
        "combo": COMBO,
        "combo_maker_price": str(COMBO_MAKER_PRICE),
        "target_locked": str(TARGET_LOCK),
        "maker_ttl_seconds": ttl,
        "order_entry": False,
        "realized": False,
        "classification": "DISARMED",
    }
    if not out["armed"]:
        print("GEMINI_EXTREME_COMBO_LIVE_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    preflight = await _fresh_preflight(ids)
    out["preflight"] = preflight
    if not preflight.get("pass"):
        out["classification"] = "LIVE_GATE_BLOCKED"
        print("GEMINI_EXTREME_COMBO_LIVE_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out

    session = GeminiTradingSession()
    h1_params = {"symbol": HEDGES[0]["symbol"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK", "price": "0.02", "quantity": "1", "eventOutcome": HEDGES[0]["outcome"], "clientOrderId": ids["h1"]}
    h2_params = {"symbol": HEDGES[1]["symbol"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK", "price": "0.02", "quantity": "1", "eventOutcome": HEDGES[1]["outcome"], "clientOrderId": ids["h2"]}
    combo_params = {"symbol": COMBO, "side": "BUY", "type": "LIMIT", "timeInForce": "MOC", "price": str(COMBO_MAKER_PRICE), "quantity": "1", "eventOutcome": "YES", "clientOrderId": ids["combo"]}
    out["order_entry"] = True
    try:
        await session.connect()
        h1 = await session.place_fok(h1_params, terminal_timeout=3.0)
        out["hedge1"] = _result_dict(h1)
        if h1.status != "FILLED" or _d(h1.filled_quantity) != QTY:
            out["classification"] = "LIVE_HEDGE1_NOT_FILLED" if h1.status != "UNKNOWN" else "LIVE_HARD_HALT_AMBIGUOUS_HEDGE1"
            print("GEMINI_EXTREME_COMBO_LIVE_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
            return out

        h2 = await session.place_fok(h2_params, terminal_timeout=3.0)
        out["hedge2"] = _result_dict(h2)
        if h2.status != "FILLED" or _d(h2.filled_quantity) != QTY:
            if h2.status == "UNKNOWN":
                out["classification"] = "LIVE_HARD_HALT_AMBIGUOUS_HEDGE2"
                print("GEMINI_EXTREME_COMBO_LIVE_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
                return out
            flat1 = await _flatten(session, h1_params)
            out["flatten1"] = _result_dict(flat1)
            out["classification"] = "LIVE_HEDGE2_FAILED_FLATTENED" if flat1.status == "FILLED" else "LIVE_HARD_HALT_FLATTEN1_FAILED"
            print("GEMINI_EXTREME_COMBO_LIVE_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
            return out

        # With both hedges filled, q1 maker .90 plus conservative 1c maker fee
        # leaves >=3c minimum settlement cover using the fixed hedge costs.
        combo = await _maker_with_ttl(session, combo_params, ttl_seconds=ttl)
        out["combo_result"] = combo
        combo_qty = _d(combo.get("filled_quantity")) or D("0")
        combo_status = str(combo.get("status") or "UNKNOWN").upper()
        if combo_status == "FILLED" and combo_qty == QTY:
            def fill_cost(result: ExecutionResult) -> D:
                p = _d(result.average_fill_price) or D("0.02")
                f = _d(result.fee_paid)
                if f is None:
                    f = _fee(D("0.07"), p)
                return p + f
            hedge_cost = fill_cost(h1) + fill_cost(h2)
            cp = _d(combo.get("average_fill_price")) or COMBO_MAKER_PRICE
            cf = _d(combo.get("fee_paid"))
            if cf is None:
                cf = _fee(D("0.0175"), cp)
            total = hedge_cost + cp + cf
            locked = ONE - total
            out["actual_or_conservative_all_in"] = str(total)
            out["modeled_locked_profit"] = str(locked)
            out["classification"] = "FILLED_NOT_SETTLED" if locked >= TARGET_LOCK else "FILLED_NOT_SETTLED_BELOW_TARGET_HARD_HALT"
            out["settlement_identity"] = "AB+(1-A)+(1-B)>=1"
            print("GEMINI_EXTREME_COMBO_LIVE_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
            return out

        if combo_status == "UNKNOWN" or combo_qty not in {D("0"), QTY}:
            out["classification"] = "LIVE_HARD_HALT_AMBIGUOUS_COMBO"
            print("GEMINI_EXTREME_COMBO_LIVE_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
            return out

        # No combo fill: return flat by selling both prehedges.
        flat2 = await _flatten(session, h2_params)
        flat1 = await _flatten(session, h1_params)
        out["flatten2"] = _result_dict(flat2)
        out["flatten1"] = _result_dict(flat1)
        if flat1.status == "FILLED" and flat2.status == "FILLED":
            out["classification"] = "LIVE_PROBE_NO_COMBO_FILL_FLATTENED"
        else:
            out["classification"] = "LIVE_HARD_HALT_FLATTEN_FAILED"
        print("GEMINI_EXTREME_COMBO_LIVE_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    except Exception as exc:
        out["classification"] = "LIVE_HARD_HALT_EXCEPTION"
        out["exception"] = f"{type(exc).__name__}:{str(exc)[:220]}"
        print("GEMINI_EXTREME_COMBO_LIVE_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(run_once())

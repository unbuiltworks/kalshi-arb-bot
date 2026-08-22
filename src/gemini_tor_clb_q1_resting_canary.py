"""Long-lived q=1 monitored maker canary for a rule-certified same-Gemini combo cover.

Design goals:
- Exactly one maker-only combo order rests at a time (no cancel/repost carousel).
- q=1 avoids partial-fill quantity economics.
- Both exact complement hedge books are rechecked continuously while the order rests.
- If the >=3c after-fee floor disappears, cancel the maker order before any hedge is bought.
- If the combo fills, immediately buy both exact complements q=1 FOK.
- Any ambiguous lifecycle hard-stops and attempts bounded flattening/reconciliation.

DISARMED unless both campaign arms match. A completed 3-leg fill is FILLED_NOT_SETTLED,
never REALIZED until settlement/reconciliation.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from decimal import Decimal
from typing import Any, Mapping

import httpx
import src.gemini_extreme_combo_live_canary as c
from src.gemini_prediction_auth import private_rest_request

D = Decimal
BASE = "https://api.gemini.com"
QTY = D("1")
COMBO = "GEMI-CMB-260823-8EC8FBCF7988E34C-CMB"
COMBO_PRICE = D("0.90")
TARGET_LOCK = D("0.03")
HEDGES = [
    {"symbol": "GEMI-MLS-2608222330-TOR-MIA-REGT-O0", "outcome": "NO", "target": D("0.02"), "emergency": D("0.08")},
    {"symbol": "GEMI-MLS-2608230030-CLB-NSH-REGS-CLB2", "outcome": "YES", "target": D("0.02"), "emergency": D("0.08")},
]


def emit(out: dict[str, Any]) -> None:
    print("GEMINI_TOR_CLB_Q1_RESTING_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)


async def positions() -> list[dict[str, Any]]:
    r = await private_rest_request("POST", "/v1/prediction-markets/positions", params={}, timeout=8.0)
    if r.status_code != 200:
        return [{"error": f"positions_http_{r.status_code}"}]
    try:
        b = r.json()
    except Exception:
        return [{"error": "positions_parse"}]
    syms = {COMBO, *(x["symbol"] for x in HEDGES)}
    return [dict(x) for x in (b.get("positions", []) if isinstance(b, Mapping) else []) if isinstance(x, Mapping) and str(x.get("symbol") or x.get("instrumentSymbol") or "") in syms]


async def book(symbol: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as h:
        r = await h.get(f"{BASE}/v1/book/{symbol}", params={"limit_bids": 20, "limit_asks": 20})
    try:
        b = r.json()
    except Exception:
        b = {}
    return b if isinstance(b, Mapping) else {}


async def hedge_state() -> dict[str, Any]:
    b1, b2 = await asyncio.gather(book(HEDGES[0]["symbol"]), book(HEDGES[1]["symbol"]))
    rows = []
    total = D("0")
    ok = True
    for spec, b in zip(HEDGES, (b1, b2)):
        top = c._outcome_ask(b, spec["outcome"])
        row: dict[str, Any] = {"symbol": spec["symbol"], "outcome": spec["outcome"]}
        if top is None:
            row["available"] = False
            ok = False
        else:
            p, depth = top
            fee = c._fee(D("0.07"), p, QTY)
            row.update({"available": True, "ask": str(p), "depth": str(depth), "fee": str(fee), "all_in": str(p + fee), "target": str(spec["target"])})
            total += p + fee
            if p > spec["target"] or depth < QTY:
                ok = False
        rows.append(row)
    maker_fee = c._fee(D("0.0175"), COMBO_PRICE, QTY)
    all_in = COMBO_PRICE + maker_fee + total
    locked = D("1") - all_in
    return {"ok": bool(ok and locked >= TARGET_LOCK), "hedges": rows, "hedge_all_in": str(total), "combo_maker_fee": str(maker_fee), "all_in": str(all_in), "locked": str(locked)}


async def buy_hedge(session: c.GeminiTradingSession, spec: dict[str, Any], cid: str) -> dict[str, Any]:
    b = await book(spec["symbol"])
    top = c._outcome_ask(b, spec["outcome"])
    if top is None:
        return {"status": "NO_Q1_ASK"}
    p, depth = top
    if depth < QTY:
        return {"status": "INSUFFICIENT_Q1_DEPTH", "ask": str(p), "depth": str(depth)}
    limit = spec["target"] if p <= spec["target"] else spec["emergency"]
    if p > limit:
        return {"status": "ASK_ABOVE_EMERGENCY_CAP", "ask": str(p), "cap": str(limit)}
    params = {"symbol": spec["symbol"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK", "price": str(limit), "quantity": "1", "eventOutcome": spec["outcome"], "clientOrderId": cid[:36]}
    res = await session.place_fok(params, terminal_timeout=3.0)
    return {"observed_ask": str(p), "depth": str(depth), "limit": str(limit), "target": str(spec["target"]), "result": res.as_dict()}


async def sell_yes_fok(session: c.GeminiTradingSession, symbol: str, cid: str) -> dict[str, Any]:
    b = await book(symbol)
    bids = []
    for x in b.get("bids", []) or []:
        if not isinstance(x, Mapping):
            continue
        try:
            p = D(str(x.get("price")))
            q = D(str(x.get("amount") if x.get("amount") is not None else x.get("quantity")))
        except Exception:
            continue
        if p > 0 and q >= QTY:
            bids.append((p, q))
    if not bids:
        return {"status": "NO_EXIT_BID"}
    p, depth = max(bids, key=lambda z: z[0])
    params = {"symbol": symbol, "side": "SELL", "type": "LIMIT", "timeInForce": "FOK", "price": str(p), "quantity": "1", "eventOutcome": "YES", "clientOrderId": cid[:36]}
    res = await session.place_fok(params, terminal_timeout=3.0)
    return {"best_bid": str(p), "depth": str(depth), "result": res.as_dict()}


async def run_once() -> dict[str, Any]:
    campaign = os.getenv("GEMINI_COMBO_CAMPAIGN_ID", "").strip()
    arm = os.getenv("GEMINI_COMBO_ARM_CAMPAIGN", "").strip()
    enabled = os.getenv("ENABLE_GEMINI_COMBO_LIVE_Q1", "").strip().upper() == "YES"
    max_lifetime = max(300, min(3600, int(os.getenv("GEMINI_COMBO_RESTING_MAX_SECONDS", "1800"))))
    monitor_seconds = max(0.5, min(5.0, float(os.getenv("GEMINI_COMBO_HEDGE_MONITOR_SECONDS", "1.0"))))
    out: dict[str, Any] = {
        "status": "GEMINI_TOR_CLB_Q1_RESTING_CANARY",
        "campaign": campaign,
        "armed": bool(enabled and campaign and arm == campaign),
        "quantity": "1",
        "combo": COMBO,
        "combo_price": str(COMBO_PRICE),
        "target_locked": str(TARGET_LOCK),
        "max_lifetime_seconds": max_lifetime,
        "hedge_monitor_seconds": monitor_seconds,
        "order_entry": False,
        "realized": False,
        "classification": "DISARMED",
    }
    if not out["armed"]:
        emit(out)
        return out

    before = await positions()
    out["positions_before"] = before
    if before:
        out["classification"] = "LIVE_GATE_BLOCKED_EXISTING_POSITION"
        emit(out)
        return out

    ids = {"combo": f"{campaign}-c"[:36], "h1": f"{campaign}-h1"[:36], "h2": f"{campaign}-h2"[:36]}
    c.COMBO = COMBO
    c.HEDGES = [{"symbol": HEDGES[0]["symbol"], "outcome": HEDGES[0]["outcome"], "max_price": HEDGES[0]["target"]}, {"symbol": HEDGES[1]["symbol"], "outcome": HEDGES[1]["outcome"], "max_price": HEDGES[1]["target"]}]
    c.COMBO_MAKER_PRICE = COMBO_PRICE
    c.TARGET_LOCK = TARGET_LOCK
    c.QTY = QTY
    pre = await c._fresh_preflight(ids)
    out["preflight"] = pre
    if not pre.get("pass"):
        out["classification"] = "LIVE_GATE_BLOCKED"
        emit(out)
        return out

    session = c.GeminiTradingSession()
    out["order_entry"] = True
    try:
        await session.connect()
        combo_params = {"symbol": COMBO, "side": "BUY", "type": "LIMIT", "timeInForce": "MOC", "price": str(COMBO_PRICE), "quantity": "1", "eventOutcome": "YES", "clientOrderId": ids["combo"]}
        maker_task = asyncio.create_task(c._maker_with_ttl(session, combo_params, ttl_seconds=max_lifetime))
        started = time.monotonic()
        stale_reason = None
        last_heartbeat = started
        while not maker_task.done():
            state = await hedge_state()
            out["last_hedge_state"] = state
            if not state.get("ok"):
                stale_reason = "HEDGE_FLOOR_DROPPED"
                try:
                    await session.request("order.cancel_session", {}, timeout=3.0)
                except Exception as exc:
                    out["cancel_exception"] = f"{type(exc).__name__}:{str(exc)[:160]}"
                break
            now = time.monotonic()
            if now - last_heartbeat >= 60:
                print("GEMINI_TOR_CLB_Q1_RESTING_HEARTBEAT " + json.dumps({"campaign": campaign, "seconds_open": round(now - started, 1), "hedge_state": state, "order_entry": True}, separators=(",", ":"), default=str), flush=True)
                last_heartbeat = now
            await asyncio.sleep(monitor_seconds)

        combo_result = await maker_task
        out["combo_result"] = combo_result
        cq = c._d(combo_result.get("filled_quantity")) or D("0")
        cs = str(combo_result.get("status") or "UNKNOWN").upper()
        if cs == "UNKNOWN" or cq not in {D("0"), QTY}:
            out["classification"] = "LIVE_HARD_HALT_AMBIGUOUS_COMBO"
            emit(out)
            return out
        if not (cs == "FILLED" and cq == QTY):
            out["classification"] = "LIVE_RESTING_CANCELED_STALE_ZERO_HEDGE_COST" if stale_reason else "LIVE_RESTING_TTL_NO_FILL_ZERO_HEDGE_COST"
            out["stale_reason"] = stale_reason
            emit(out)
            return out

        h1, h2 = await asyncio.gather(buy_hedge(session, HEDGES[0], ids["h1"]), buy_hedge(session, HEDGES[1], ids["h2"]))
        out["hedge1"] = h1
        out["hedge2"] = h2
        r1 = h1.get("result") if isinstance(h1, Mapping) else None
        r2 = h2.get("result") if isinstance(h2, Mapping) else None
        ok1 = isinstance(r1, Mapping) and str(r1.get("status") or "") == "FILLED" and D(str(r1.get("filled_quantity") or "0")) == QTY
        ok2 = isinstance(r2, Mapping) and str(r2.get("status") or "") == "FILLED" and D(str(r2.get("filled_quantity") or "0")) == QTY
        if not (ok1 and ok2):
            if ok1:
                out["hedge1_flatten"] = await sell_yes_fok(session, HEDGES[0]["symbol"], f"{campaign}-fh1")
            if ok2:
                out["hedge2_flatten"] = await sell_yes_fok(session, HEDGES[1]["symbol"], f"{campaign}-fh2")
            out["combo_flatten"] = await sell_yes_fok(session, COMBO, f"{campaign}-fc")
            out["classification"] = "LIVE_HEDGE_FAILURE_AFTER_COMBO_FILL_HARD_HALT"
            emit(out)
            return out

        def cost(r: Mapping[str, Any], fallback: D) -> D:
            p = c._d(r.get("average_fill_price")) or fallback
            f = c._d(r.get("fee_paid"))
            f = f if f is not None else c._fee(D("0.07"), p, QTY)
            return p + f

        cp = c._d(combo_result.get("average_fill_price")) or COMBO_PRICE
        cf = c._d(combo_result.get("fee_paid"))
        cf = cf if cf is not None else c._fee(D("0.0175"), cp, QTY)
        total = cp + cf + cost(r1, c._d(h1.get("observed_ask")) or HEDGES[0]["target"]) + cost(r2, c._d(h2.get("observed_ask")) or HEDGES[1]["target"])
        locked = D("1") - total
        out["actual_or_conservative_all_in"] = str(total)
        out["locked_profit"] = str(locked)
        out["classification"] = "FILLED_NOT_SETTLED" if locked >= TARGET_LOCK else "FILLED_NOT_SETTLED_BELOW_TARGET"
        out["settlement_identity"] = "AB+(1-A)+(1-B)>=1"
        emit(out)
        return out
    except Exception as exc:
        out["classification"] = "LIVE_HARD_HALT_EXCEPTION"
        out["exception"] = f"{type(exc).__name__}:{str(exc)[:220]}"
        emit(out)
        return out
    finally:
        await session.close()


if __name__ == "__main__":
    print("GEMINI_TOR_CLB_Q1_RESTING_BOOT", flush=True)
    asyncio.run(run_once())

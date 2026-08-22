"""Bounded q=1 live canary for No Albon + No Morikawa Gemini combo.

The combo definition is created/retrieved first (not a trade). Live mutation then
requires both campaign arms and the inherited fresh terms/balance/idempotency/
book/locked-profit gate. Uses exact per-leg FOK caps: Albon YES 2c, Morikawa YES 3c.
"""
from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from typing import Any, Mapping

import httpx
import src.gemini_extreme_combo_live_canary as c
from src.gemini_prediction_auth import credentials, has_credentials, rest_auth_headers

D = Decimal
PATH = "/v1/prediction-markets/combos"
BASE = "https://api.gemini.com"
LEGS = [
    {"contractId": "396739", "requiredOutcome": "No"},
    {"contractId": "478679", "requiredOutcome": "No"},
]
HEDGES = [
    {"symbol": "GEMI-F1-DUTGP-WIN-20260823-ALB", "outcome": "YES", "max_price": D("0.02")},
    {"symbol": "GEMI-GOLF-BMW-WIN-20260823-MORIKAWA", "outcome": "YES", "max_price": D("0.03")},
]
COMBO_MAKER_PRICE = D("0.89")
TARGET_LOCK = D("0.03")
QTY = D("1")


async def _combo_symbol() -> tuple[str | None, dict[str, Any]]:
    meta: dict[str, Any] = {"combo_definition_write": True, "order_entry": False}
    if not has_credentials():
        meta["error"] = "NO_CREDENTIALS"
        return None, meta
    key, secret = credentials()
    payload = {"legs": LEGS}
    headers = rest_auth_headers(key, secret, PATH, params=payload)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=20.0) as h:
        r = await h.post(f"{BASE}{PATH}", headers=headers, json=payload)
    try:
        body = r.json()
    except Exception:
        body = {}
    combo = body.get("combo") if isinstance(body, Mapping) and isinstance(body.get("combo"), Mapping) else {}
    symbol = str(combo.get("instrumentSymbol") or "") or None
    meta.update({
        "http_status": r.status_code,
        "already_existed": body.get("alreadyExisted") if isinstance(body, Mapping) else None,
        "instrument_symbol": symbol,
        "instrument_registered": combo.get("instrumentRegistered"),
        "combo_status": combo.get("status"),
        "error_summary": str(body.get("message") or body.get("error") or "")[:180] if isinstance(body, Mapping) else "",
    })
    if r.status_code not in {200, 201} or not symbol or combo.get("instrumentRegistered") is not True or str(combo.get("status") or "").lower() != "active":
        return None, meta
    return symbol, meta


def _print(out: dict[str, Any]) -> None:
    print("GEMINI_ALBON_MORIKAWA_LIVE_CANARY " + json.dumps(out, separators=(",", ":"), default=str), flush=True)


async def run_once() -> dict[str, Any]:
    combo, create_meta = await _combo_symbol()
    campaign = os.getenv("GEMINI_COMBO_CAMPAIGN_ID", "").strip()
    armed_campaign = os.getenv("GEMINI_COMBO_ARM_CAMPAIGN", "").strip()
    enabled = os.getenv("ENABLE_GEMINI_COMBO_LIVE_Q1", "").strip().upper() == "YES"
    ttl = max(30, min(300, int(os.getenv("GEMINI_COMBO_MAKER_TTL_SECONDS", "120"))))
    out: dict[str, Any] = {
        "status": "GEMINI_ALBON_MORIKAWA_LIVE_CANARY",
        "campaign": campaign,
        "armed": bool(enabled and campaign and armed_campaign == campaign),
        "quantity": "1",
        "combo": combo,
        "combo_create": create_meta,
        "combo_maker_price": str(COMBO_MAKER_PRICE),
        "target_locked": str(TARGET_LOCK),
        "hedge_caps": [str(x["max_price"]) for x in HEDGES],
        "maker_ttl_seconds": ttl,
        "order_entry": False,
        "realized": False,
        "classification": "DISARMED",
    }
    if not combo:
        out["classification"] = "COMBO_DEFINITION_BLOCKED"
        _print(out)
        return out

    c.COMBO = combo
    c.HEDGES = HEDGES
    c.COMBO_MAKER_PRICE = COMBO_MAKER_PRICE
    c.TARGET_LOCK = TARGET_LOCK
    c.QTY = QTY

    if not out["armed"]:
        _print(out)
        return out

    ids = {"h1": f"{campaign}-h1"[:36], "h2": f"{campaign}-h2"[:36], "combo": f"{campaign}-c"[:36]}
    preflight = await c._fresh_preflight(ids)
    out["preflight"] = preflight
    if not preflight.get("pass"):
        out["classification"] = "LIVE_GATE_BLOCKED"
        _print(out)
        return out

    session = c.GeminiTradingSession()
    h1_params = {"symbol": HEDGES[0]["symbol"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK", "price": str(HEDGES[0]["max_price"]), "quantity": "1", "eventOutcome": HEDGES[0]["outcome"], "clientOrderId": ids["h1"]}
    h2_params = {"symbol": HEDGES[1]["symbol"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK", "price": str(HEDGES[1]["max_price"]), "quantity": "1", "eventOutcome": HEDGES[1]["outcome"], "clientOrderId": ids["h2"]}
    combo_params = {"symbol": combo, "side": "BUY", "type": "LIMIT", "timeInForce": "MOC", "price": str(COMBO_MAKER_PRICE), "quantity": "1", "eventOutcome": "YES", "clientOrderId": ids["combo"]}
    out["order_entry"] = True
    try:
        await session.connect()
        h1 = await session.place_fok(h1_params, terminal_timeout=3.0)
        out["hedge1"] = c._result_dict(h1)
        if h1.status != "FILLED" or c._d(h1.filled_quantity) != QTY:
            out["classification"] = "LIVE_HEDGE1_NOT_FILLED" if h1.status != "UNKNOWN" else "LIVE_HARD_HALT_AMBIGUOUS_HEDGE1"
            _print(out)
            return out

        h2 = await session.place_fok(h2_params, terminal_timeout=3.0)
        out["hedge2"] = c._result_dict(h2)
        if h2.status != "FILLED" or c._d(h2.filled_quantity) != QTY:
            if h2.status == "UNKNOWN":
                out["classification"] = "LIVE_HARD_HALT_AMBIGUOUS_HEDGE2"
                _print(out)
                return out
            flat1 = await c._flatten(session, h1_params)
            out["flatten1"] = c._result_dict(flat1)
            out["classification"] = "LIVE_HEDGE2_FAILED_FLATTENED" if flat1.status == "FILLED" else "LIVE_HARD_HALT_FLATTEN1_FAILED"
            _print(out)
            return out

        combo_result = await c._maker_with_ttl(session, combo_params, ttl_seconds=ttl)
        out["combo_result"] = combo_result
        combo_qty = c._d(combo_result.get("filled_quantity")) or D("0")
        combo_status = str(combo_result.get("status") or "UNKNOWN").upper()
        if combo_status == "FILLED" and combo_qty == QTY:
            def fill_cost(result: Any, fallback: D) -> D:
                p = c._d(result.average_fill_price) or fallback
                f = c._d(result.fee_paid)
                if f is None:
                    f = c._fee(D("0.07"), p)
                return p + f
            hedge_cost = fill_cost(h1, HEDGES[0]["max_price"]) + fill_cost(h2, HEDGES[1]["max_price"])
            cp = c._d(combo_result.get("average_fill_price")) or COMBO_MAKER_PRICE
            cf = c._d(combo_result.get("fee_paid"))
            if cf is None:
                cf = c._fee(D("0.0175"), cp)
            total = hedge_cost + cp + cf
            locked = D("1") - total
            out["actual_or_conservative_all_in"] = str(total)
            out["modeled_locked_profit"] = str(locked)
            out["classification"] = "FILLED_NOT_SETTLED" if locked >= TARGET_LOCK else "FILLED_NOT_SETTLED_BELOW_TARGET_HARD_HALT"
            out["settlement_identity"] = "AB+(1-A)+(1-B)>=1"
            _print(out)
            return out

        if combo_status == "UNKNOWN" or combo_qty not in {D("0"), QTY}:
            out["classification"] = "LIVE_HARD_HALT_AMBIGUOUS_COMBO"
            _print(out)
            return out

        flat2 = await c._flatten(session, h2_params)
        flat1 = await c._flatten(session, h1_params)
        out["flatten2"] = c._result_dict(flat2)
        out["flatten1"] = c._result_dict(flat1)
        out["classification"] = "LIVE_PROBE_NO_COMBO_FILL_FLATTENED" if flat1.status == "FILLED" and flat2.status == "FILLED" else "LIVE_HARD_HALT_FLATTEN_FAILED"
        _print(out)
        return out
    except Exception as exc:
        out["classification"] = "LIVE_HARD_HALT_EXCEPTION"
        out["exception"] = f"{type(exc).__name__}:{str(exc)[:220]}"
        _print(out)
        return out
    finally:
        await session.close()


if __name__ == "__main__":
    print("GEMINI_ALBON_MORIKAWA_LIVE_BOOT", flush=True)
    asyncio.run(run_once())

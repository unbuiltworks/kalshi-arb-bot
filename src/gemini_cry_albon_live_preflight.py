"""Read-only q=1 preflight for Crystal Palace NO + Alexander Albon NO combo cover.

No order entry. Verifies terms, USD, no active combo order, exact q1 complement
books/depth, and conservative >=3c maker economics.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal, ROUND_CEILING
from typing import Any, Mapping

import httpx

from src.gemini_prediction_auth import private_rest_request

D = Decimal
ONE = D("1")
BASE = "https://api.gemini.com"
COMBO = "GEMI-CMB-260823-5067A7AC7F468773-CMB"
HEDGES = [
    {"symbol": "GEMI-EPL-2608221400-CRY-EVE-M-CRY", "outcome": "YES", "max_price": D("0.02")},
    {"symbol": "GEMI-F1-DUTGP-WIN-20260823-ALB", "outcome": "YES", "max_price": D("0.02")},
]
MAKER_PRICE = D("0.90")
TARGET = D("0.03")


def fee(rate: D, p: D, qty: D = ONE) -> D:
    raw = rate * qty * p * (ONE - p)
    return (raw * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def parse_level(x: Any):
    if not isinstance(x, Mapping):
        return None
    try:
        p = D(str(x.get("price")))
        q = D(str(x.get("amount") if x.get("amount") is not None else x.get("quantity")))
    except Exception:
        return None
    if q <= 0 or p <= 0 or p >= 1:
        return None
    return p, q


def outcome_ask(book: Mapping[str, Any], outcome: str):
    asks = [z for z in (parse_level(x) for x in (book.get("asks") or [])) if z]
    bids = [z for z in (parse_level(x) for x in (book.get("bids") or [])) if z]
    if outcome == "YES":
        return min(asks, key=lambda z: z[0]) if asks else None
    if outcome == "NO":
        if not bids:
            return None
        bp, bq = max(bids, key=lambda z: z[0])
        return ONE - bp, bq
    return None


async def run_once() -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "GEMINI_CRY_ALBON_LIVE_PREFLIGHT",
        "order_entry": False,
        "combo": COMBO,
        "maker_price": str(MAKER_PRICE),
        "target_locked": str(TARGET),
        "rule_certification": "GEMINI_COMBO_PRODUCT_PLUS_EXACT_SAME_INSTRUMENT_COMPLEMENTS",
    }
    terms = await private_rest_request("GET", "/v1/prediction-markets/terms/status", timeout=5.0)
    out["terms_http"] = terms.status_code
    try:
        tb = terms.json()
    except Exception:
        tb = {}
    out["terms_accepted"] = bool(tb.get("hasAcceptedLatest")) if isinstance(tb, Mapping) else False

    bal = await private_rest_request("POST", "/v1/balances", timeout=5.0)
    out["balance_http"] = bal.status_code
    usd = D("0")
    try:
        rows = bal.json()
    except Exception:
        rows = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping) and str(row.get("currency") or "").upper() == "USD":
                try:
                    usd = D(str(row.get("available") or "0"))
                except Exception:
                    usd = D("0")
                break
    out["usd_available"] = str(usd)

    active = await private_rest_request("POST", "/v1/prediction-markets/orders/active", params={"symbol": COMBO, "limit": 100, "offset": 0}, timeout=5.0)
    out["active_orders_http"] = active.status_code
    try:
        ab = active.json()
    except Exception:
        ab = {}
    active_rows = (ab.get("orders") or []) if isinstance(ab, Mapping) else []
    out["active_combo_orders"] = len(active_rows)

    symbols = [COMBO] + [x["symbol"] for x in HEDGES]
    books: dict[str, Any] = {}
    statuses: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=8.0) as h:
        async def fetch(symbol: str) -> None:
            r = await h.get(f"{BASE}/v1/book/{symbol}", params={"limit_bids": 20, "limit_asks": 20})
            statuses[symbol] = r.status_code
            try:
                books[symbol] = r.json()
            except Exception:
                books[symbol] = {}
        await asyncio.gather(*(fetch(s) for s in symbols))

    hedge_rows = []
    hedge_total = D("0")
    hedge_ok = True
    for spec in HEDGES:
        top = outcome_ask(books.get(spec["symbol"], {}), spec["outcome"])
        row: dict[str, Any] = {"symbol": spec["symbol"], "outcome": spec["outcome"], "http": statuses.get(spec["symbol"])}
        if top is None:
            row["available"] = False
            hedge_ok = False
        else:
            p, q = top
            f = fee(D("0.07"), p)
            row.update({"available": True, "ask": str(p), "depth": str(q), "fee": str(f), "all_in": str(p + f)})
            hedge_total += p + f
            if p > spec["max_price"] or q < ONE:
                hedge_ok = False
        hedge_rows.append(row)

    combo_book = books.get(COMBO, {}) if isinstance(books.get(COMBO, {}), Mapping) else {}
    out["combo_book"] = {
        "http": statuses.get(COMBO),
        "bids": len(combo_book.get("bids") or []),
        "asks": len(combo_book.get("asks") or []),
    }
    maker_fee = fee(D("0.0175"), MAKER_PRICE)
    total = MAKER_PRICE + maker_fee + hedge_total
    locked = ONE - total
    out.update({
        "hedges": hedge_rows,
        "hedge_total": str(hedge_total),
        "hedge_depth_q1_ok": hedge_ok,
        "maker_fee_conservative": str(maker_fee),
        "all_in_if_filled_now": str(total),
        "locked_if_filled_now": str(locked),
        "max_prehedge_exposure": "0.06",
    })
    out["preflight_pass"] = bool(
        out["terms_accepted"]
        and usd >= ONE
        and out["active_combo_orders"] == 0
        and hedge_ok
        and locked >= TARGET
    )
    print("GEMINI_CRY_ALBON_LIVE_PREFLIGHT " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())

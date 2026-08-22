"""Production combo supervisor v34: q5 shadow economics on custom MVE replay pool.

Adds read-only quantity-aware q=5 RFQ scoring for a bounded subset of v33's replay
pool. This can reveal fee-rounding amortization or quantity quote effects without
arming q5 execution. The normal returned candidate set remains q1-only, so the
existing live executor and all q1 safety gates are unchanged.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_CEILING
import json
import os
from typing import Any

from src import sports_kp_live_combo_supervisor_v33 as v33
from src import sports_kp_live_combo_supervisor_v2 as base2
from src import sports_kp_mve_same_venue_scan as scan
from src.auth import KalshiClient

D = Decimal
ONE = D("1")
CENT = D("0.01")
_ORIGINAL_SCAN = base2.scan_existing_mves


def _fee_total(price: Decimal, qty: int, rate: Decimal = D("0.07")) -> Decimal:
    if price <= 0 or price >= 1 or qty <= 0:
        return D("0")
    raw = rate * D(qty) * price * (ONE - price)
    return (raw / CENT).to_integral_value(rounding=ROUND_CEILING) * CENT


async def _q5_quotes(client: KalshiClient, ticker: str, wait_s: float = 1.75) -> tuple[str | None, list[dict[str, Any]]]:
    r = await client.post("/communications/rfqs", json={
        "market_ticker": ticker, "contracts": 5, "rest_remainder": False, "replace_existing": True,
    })
    if r.status_code >= 300:
        return None, []
    body = r.json() if r.content else {}
    rid = str(body.get("id") or (body.get("rfq") or {}).get("id") or "") if isinstance(body, dict) else ""
    if not rid:
        return None, []
    deadline = asyncio.get_running_loop().time() + wait_s
    quotes: list[dict[str, Any]] = []
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.25)
        qr = await client.get("/communications/quotes", params={"rfq_id": rid, "rfq_user_filter": "self", "limit": 100})
        if qr.status_code == 200:
            qb = qr.json() if qr.content else {}
            quotes = [dict(x) for x in (qb.get("quotes") or []) if isinstance(x, dict)] if isinstance(qb, dict) else []
    return rid, quotes


async def _score_q5(client: KalshiClient, ticker: str) -> dict[str, Any] | None:
    mr = await client.get(f"/markets/{ticker}")
    if mr.status_code != 200:
        return None
    m = scan._market(mr.json() if mr.content else {})
    if str(m.get("status") or "").lower() not in {"active", "open"}:
        return None
    legs = [dict(x) for x in (m.get("mve_selected_legs") or []) if isinstance(x, dict)]
    if len(legs) not in scan.SUPPORTED_LEG_COUNTS or not scan._sportsish(legs):
        return None

    hedge_total = D("0")
    components: list[dict[str, Any]] = []
    for leg in legs:
        st = str(leg.get("market_ticker") or "")
        selected = str(leg.get("side") or "").lower()
        sr = await client.get(f"/markets/{st}") if st else None
        sm = scan._market(sr.json() if sr is not None and sr.content else {}) if sr is not None else {}
        h = scan._hedge_quote(sm, selected)
        if not h.get("ok"):
            return None
        px = D(str(h["price"])); depth = D(str(h["depth"]))
        if depth < D("5"):
            return None
        fee_total = _fee_total(px, 5)
        hedge_total += px * D("5") + fee_total
        components.append({
            "ticker": st, "hedge_side": h["hedge_side"], "price": str(px),
            "depth_fp": str(depth), "q5_fee_total": str(fee_total),
        })

    rid, quotes = await _q5_quotes(client, ticker)
    try:
        best: dict[str, Any] | None = None
        for q in quotes:
            if str(q.get("status") or "").lower() not in {"", "open"}:
                continue
            nb = scan._d(q.get("no_bid_dollars"))
            if nb is None or not (D("0") < nb < ONE):
                continue
            combo_px = ONE - nb
            if best is None or combo_px < best["combo_price"]:
                best = {"quote_id": str(q.get("id") or ""), "combo_price": combo_px, "updated_ts": q.get("updated_ts")}
        if best is None:
            return {"ticker": ticker, "classification": "Q5_NO_QUOTE", "quote_count": len(quotes)}
        combo_fee_total = _fee_total(best["combo_price"], 5)
        total_cost = hedge_total + best["combo_price"] * D("5") + combo_fee_total
        total_locked = D("5") - total_cost
        per_contract = total_locked / D("5")
        return {
            "ticker": ticker,
            "title": m.get("title") or m.get("yes_sub_title"),
            "leg_count": len(legs),
            "components": components,
            "quote_count": len(quotes),
            "combo_price": str(best["combo_price"]),
            "combo_q5_fee_total": str(combo_fee_total),
            "q5_total_cost": str(total_cost),
            "q5_total_locked_profit": str(total_locked),
            "q5_locked_profit_per_contract": str(per_contract),
            "classification": "Q5_SHADOW_EXECUTABLE" if per_contract >= D("0.03") else "Q5_SHADOW_NO_TRADE",
            "q5_order_entry": False,
        }
    finally:
        if rid:
            try:
                await client.delete(f"/communications/rfqs/{rid}")
            except Exception:
                pass


async def _scan_with_q5_shadow() -> dict[str, Any]:
    q1 = await _ORIGINAL_SCAN()
    limit = max(1, min(int(os.getenv("MVE_REPLAY_Q5_SHADOW_MAX_RFQS", "3")), 6))
    rows: list[dict[str, Any]] = []
    client = KalshiClient(base_url=scan.BASE)
    try:
        for ticker in sorted(v33._REPLAY)[:limit]:
            try:
                row = await _score_q5(client, ticker)
            except Exception:
                row = None
            if row:
                rows.append(row)
    finally:
        await client.close()
    rows.sort(key=lambda r: D(str(r.get("q5_locked_profit_per_contract") or "-9")), reverse=True)
    v33.v32.v31.logger.warning("SPORTS_MVE_REPLAY_Q5_SHADOW %s", json.dumps({
        "pool_size": len(v33._REPLAY),
        "rfqs_scored": len(rows),
        "shadow_executable": sum(r.get("classification") == "Q5_SHADOW_EXECUTABLE" for r in rows),
        "best": rows[0] if rows else None,
        "live_q5_enabled": False,
        "live_threshold_reference": "0.03_per_contract",
        "new_market_creation": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    q1["q5_shadow"] = rows
    return q1


base2.scan_existing_mves = _scan_with_q5_shadow

run = v33.run
main = v33.main

if __name__ == "__main__":
    main()

"""One-shot authoritative reconciliation for expired unpaired crypto-basis fills.

This recovery tool NEVER submits or cancels venue orders. It only:
1) locates basis-risk ORDER_SUBMITTED trades lacking TRADE_REALIZED,
2) requires exactly one recorded Kalshi fill and no Gemini fill,
3) requires the 15-minute window to be long expired,
4) reads the official Kalshi market result,
5) computes actual one-leg P&L from the recorded fill and fee, and
6) posts an idempotent TRADE_REALIZED incident event to the ARB ledger.

It deliberately does not attempt a late hedge after expiry.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import logging
import os
from typing import Any

import httpx
import psycopg

from src.gemini_kalshi_fast_overlap import KALSHI_REST

D = Decimal
logger = logging.getLogger("kalshi_arb.crypto_basis_unpaired_reconciler_once")
STRATEGY = "gemini_kalshi_15m_crypto_basis_risk"
DATABASE_URL_ENV = "DATABASE_URL"
LEDGER_URL_ENV = "ARB_LEDGER_EVENTS_URL"
LEDGER_TOKEN_ENV = "LEDGER_INGEST_TOKEN"
MIN_AGE = timedelta(minutes=30)


def _connect():
    url = os.getenv(DATABASE_URL_ENV, "").strip()
    if not url:
        raise PermissionError("DATABASE_URL required")
    return psycopg.connect(url, connect_timeout=5)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_unresolved() -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.trade_id, s.event_ts, s.market, s.asset, s.mode, s.metadata,
                   f.price, f.fee, f.quantity, f.metadata
            FROM public.trade_events s
            JOIN public.trade_events f
              ON f.trade_id=s.trade_id AND f.event_type='FILL' AND lower(COALESCE(f.side,''))='kalshi'
            WHERE s.strategy=%s
              AND s.event_type='ORDER_SUBMITTED'
              AND s.status='ONE_LEG_FILLED'
              AND NOT EXISTS (
                SELECT 1 FROM public.trade_events r
                WHERE r.trade_id=s.trade_id AND r.event_type='TRADE_REALIZED'
              )
              AND NOT EXISTS (
                SELECT 1 FROM public.trade_events g
                WHERE g.trade_id=s.trade_id AND g.event_type='FILL'
                  AND lower(COALESCE(g.side,''))='gemini'
              )
            ORDER BY s.event_ts
            """,
            (STRATEGY,),
        )
        rows = []
        for trade_id,event_ts,market,asset,mode,smeta,price,fee,qty,fmeta in cur.fetchall():
            rows.append({
                "trade_id": str(trade_id), "event_ts": event_ts, "market": market,
                "asset": asset, "mode": mode, "submitted_metadata": smeta or {},
                "price": D(str(price)), "fee": D(str(fee or 0)),
                "quantity": D(str(qty or 0)), "fill_metadata": fmeta or {},
            })
        return rows


def _context(row: dict[str, Any]) -> dict[str, Any]:
    meta = dict(row["submitted_metadata"] or {})
    ctx = meta.get("basis_risk_context")
    if not isinstance(ctx, dict):
        raise RuntimeError(f"{row['trade_id']} missing basis context")
    return ctx


def _purchased_side(ctx: dict[str, Any]) -> str:
    direction = str(ctx.get("dominance_safe_direction") or ctx.get("direction") or "").upper()
    if direction == "KALSHI_YES_GEMINI_NO":
        return "yes"
    if direction == "KALSHI_NO_GEMINI_YES":
        return "no"
    raise RuntimeError(f"unsupported dominance direction {direction!r}")


async def _post(http: httpx.AsyncClient, payload: dict[str, Any]) -> None:
    url = os.getenv(LEDGER_URL_ENV, "").strip()
    token = os.getenv(LEDGER_TOKEN_ENV, "").strip()
    if not url or not token:
        raise PermissionError("ledger ingest URL/token required")
    r = await http.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
    r.raise_for_status()


async def _reconcile_one(http: httpx.AsyncClient, row: dict[str, Any]) -> dict[str, Any]:
    if datetime.now(timezone.utc) - row["event_ts"].astimezone(timezone.utc) < MIN_AGE:
        raise RuntimeError(f"{row['trade_id']} is too recent for settlement-only recovery")
    if row["quantity"] != D("1"):
        raise RuntimeError(f"{row['trade_id']} unexpected quantity {row['quantity']}")
    ctx = _context(row)
    ticker = str(ctx.get("kalshi_ticker") or row["market"].split("|")[-1]).strip()
    if not ticker:
        raise RuntimeError(f"{row['trade_id']} missing Kalshi ticker")
    purchased = _purchased_side(ctx)

    r = await http.get(f"{KALSHI_REST}/markets/{ticker}", timeout=10.0)
    r.raise_for_status()
    body = r.json()
    market = body.get("market", body) if isinstance(body, dict) else {}
    outcome = str(market.get("result") or "").lower()
    status = str(market.get("status") or "").lower()
    if outcome not in {"yes", "no"}:
        raise RuntimeError(f"{ticker} has no authoritative yes/no result; status={status}")

    yes_space = row["price"]
    raw_cost = yes_space if purchased == "yes" else D("1") - yes_space
    capital = raw_cost + row["fee"]
    payout = D("1") if purchased == outcome else D("0")
    gross_pnl = payout - raw_cost
    net_pnl = payout - capital
    trade_id = row["trade_id"]
    payload = {
        "event_id": f"{trade_id}-realized-unpaired-recovery",
        "trade_id": trade_id,
        "event_type": "TRADE_REALIZED",
        "event_ts": _iso(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": row["market"],
        "asset": row["asset"],
        "venues": ["Gemini", "Kalshi"],
        "side": f"Kalshi {purchased.upper()} ONLY",
        "status": "REALIZED_UNPAIRED_SETTLED",
        "quantity": "1",
        "amount": str(capital),
        "fee": str(row["fee"]),
        "gross_pnl": str(gross_pnl),
        "net_pnl": str(net_pnl),
        "legs_required": 2,
        "legs_filled": 1,
        "metadata": {
            "classification": "LIVE_BASIS_RISK_RESEARCH_ONE_LEG_SETTLEMENT_INCIDENT",
            "risk_class": "SETTLEMENT_BASIS_RESEARCH",
            "guaranteed_arb": False,
            "recovery": "EXPIRED_POSITION_OFFICIAL_KALSHI_SETTLEMENT",
            "late_hedge_attempted": False,
            "kalshi_ticker": ticker,
            "kalshi_status": status,
            "kalshi_outcome": outcome,
            "purchased_kalshi_side": purchased,
            "yes_space_fill_price": str(yes_space),
            "raw_cost": str(raw_cost),
            "basis_risk_context": ctx,
            "incident_requires_execution_review": True,
        },
    }
    await _post(http, payload)
    return {"trade_id": trade_id, "ticker": ticker, "outcome": outcome, "purchased": purchased, "net_pnl": str(net_pnl)}


async def main_async() -> None:
    rows = _load_unresolved()
    if not rows:
        logger.warning("CRYPTO_BASIS_UNPAIRED_RECONCILER no_unresolved=true order_entry=false")
        return
    async with httpx.AsyncClient() as http:
        results = []
        for row in rows:
            results.append(await _reconcile_one(http, row))
    logger.warning("CRYPTO_BASIS_UNPAIRED_RECONCILED count=%s results=%s order_entry=false", len(results), results)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

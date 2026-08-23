"""One-off fail-closed recovery for gkbasis-br0823131801-01.

This module exists only to neutralize the orphaned Gemini SOL NO q=1 leg created when
Kalshi rejected the paired FOK. It never opens a new speculative trade. Recovery order:
(1) try to SELL the orphaned Gemini NO with an aggressive LIMIT FOK; (2) if Gemini has
no immediate bid, restore the intended hedge by buying exactly q=1 Kalshi YES with a
max-price LIMIT FOK. The second path can lock a small loss but removes binary exposure.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
import uuid

from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor
from src.gemini_prediction_auth import private_rest_request

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_orphan_recovery")

TRADE_ID = "gkbasis-br0823131801-01"
SYMBOL = "GEMI-SOL15M2608231330-UP"
TICKER = "KXSOL15M-26AUG230930-30"
OUTCOME = "NO"
ORIGINAL_GEMINI_CLIENT_ID = "solc2-br0823131801-01-g"
ORIGINAL_KALSHI_CLIENT_ID = "solc2-br0823131801-01-k"


def _d(v) -> Decimal:
    try:
        return D(str(v or "0"))
    except Exception:
        return D("0")


async def _gemini_filled_orders() -> list[dict]:
    now_ms = int(time.time() * 1000)
    response = await private_rest_request(
        "POST", "/v1/prediction-markets/orders/history",
        params={"status":"filled","symbol":SYMBOL,"limit":100,"from":now_ms-60*60*1000,"to":now_ms+1000},
        timeout=4.0,
    )
    if response.status_code != 200:
        raise RuntimeError(f"gemini history status={response.status_code}")
    return list(response.json().get("orders", []))


def _row_client_id(row: dict) -> str:
    return str(row.get("clientOrderId") or row.get("client_order_id") or row.get("client_orderid") or "")


def _row_symbol(row: dict) -> str:
    return str(row.get("symbol") or row.get("marketSymbol") or row.get("market_symbol") or "").upper()


def _row_outcome(row: dict) -> str:
    return str(row.get("eventOutcome") or row.get("event_outcome") or row.get("outcome") or row.get("contractOutcome") or row.get("contract_outcome") or "").upper()


def _row_side(row: dict) -> str:
    return str(row.get("side") or row.get("orderSide") or row.get("order_side") or "").upper()


def _row_qty(row: dict) -> Decimal:
    return _d(row.get("filledQuantity") or row.get("filled_quantity") or row.get("executedQuantity") or row.get("executed_quantity") or row.get("quantity"))


def _gemini_net_no(rows: list[dict]) -> Decimal:
    net = D("0")
    for row in rows:
        if _row_symbol(row) != SYMBOL:
            continue
        outcome = _row_outcome(row)
        if outcome and outcome != OUTCOME:
            continue
        qty = _row_qty(row)
        side = _row_side(row)
        if side == "BUY": net += qty
        elif side == "SELL": net -= qty
    return net


async def _kalshi_original_fill(executor: KalshiV2Executor) -> Decimal:
    response = await executor.client.get("/portfolio/orders", params={"ticker":TICKER,"limit":100})
    if response.status_code != 200:
        raise RuntimeError(f"kalshi orders status={response.status_code}")
    for row in response.json().get("orders", []):
        if str(row.get("client_order_id") or "") == ORIGINAL_KALSHI_CLIENT_ID:
            return _d(row.get("fill_count_fp") or row.get("fill_count") or "0")
    return D("0")


def _safe_order_view(row: dict) -> dict:
    return {"client_id":_row_client_id(row),"symbol":_row_symbol(row),"outcome":_row_outcome(row),"side":_row_side(row),"filled_quantity":str(_row_qty(row)),"status":str(row.get("status") or ""),"avg_execution_price":str(row.get("avgExecutionPrice") or row.get("averageFillPrice") or row.get("price") or "")}


async def main_async() -> None:
    kalshi = KalshiV2Executor()
    gemini = GeminiTradingSession()
    try:
        kalshi_fill = await _kalshi_original_fill(kalshi)
        rows = await _gemini_filled_orders()
        original = [r for r in rows if _row_client_id(r) == ORIGINAL_GEMINI_CLIENT_ID]
        net_no = _gemini_net_no(rows)
        logger.critical("CRYPTO_BASIS_ORPHAN_RECONCILE %s", json.dumps({"trade_id":TRADE_ID,"symbol":SYMBOL,"ticker":TICKER,"kalshi_original_fill":str(kalshi_fill),"gemini_original":[_safe_order_view(r) for r in original],"gemini_net_no":str(net_no),"new_pair_trade_authorized":False},separators=(",",":")))
        if kalshi_fill > 0:
            logger.critical("CRYPTO_BASIS_ORPHAN_RECOVERY_DONE reason=KALSHI_ALREADY_FILLED")
            return
        if not original or sum((_row_qty(r) for r in original),D("0")) != D("1"):
            raise RuntimeError("original Gemini q1 fill not proven")
        if net_no <= 0:
            logger.critical("CRYPTO_BASIS_ORPHAN_RECOVERY_DONE reason=NO_OPEN_GEMINI_NO_EXPOSURE")
            return
        if net_no != D("1"):
            raise RuntimeError(f"unexpected Gemini NO net exposure {net_no}")

        await gemini.connect()
        flatten_payload={"symbol":SYMBOL,"side":"SELL","type":"LIMIT","timeInForce":"FOK","price":"0.01","quantity":"1","eventOutcome":OUTCOME,"clientOrderId":f"orphan-recovery-g-{uuid.uuid4().hex[:16]}"}
        g=await gemini.place_fok(flatten_payload,terminal_timeout=2.0)
        logger.critical("CRYPTO_BASIS_ORPHAN_GEMINI_FLATTEN_RESULT %s",json.dumps(g.as_dict(),separators=(",",":"),default=str))
        if g.status=="FILLED" and _d(g.filled_quantity)==D("1"):
            logger.critical("CRYPTO_BASIS_ORPHAN_RECOVERY_VERIFIED mode=GEMINI_FLATTEN")
            return

        # No immediate Gemini bid: restore the intended opposite leg instead. The 0.99
        # max price is intentionally aggressive because this is exposure reduction, not
        # a new opportunity. FOK ensures either exactly q=1 is hedged or nothing changes.
        hedge_payload={"ticker":TICKER,"client_order_id":f"orphan-recovery-k-{uuid.uuid4().hex[:16]}","side":"bid","count":"1.00","price":"0.99","time_in_force":"fill_or_kill","self_trade_prevention_type":"taker_at_cross","post_only":False,"cancel_order_on_pause":True,"reduce_only":False,"subaccount":0,"exchange_index":0}
        k=await kalshi.place(hedge_payload)
        logger.critical("CRYPTO_BASIS_ORPHAN_KALSHI_HEDGE_RESULT %s",json.dumps(k.as_dict(),separators=(",",":"),default=str))
        if k.status!="FILLED" or _d(k.filled_quantity)!=D("1"):
            raise RuntimeError(f"emergency Kalshi hedge did not fill status={k.status} qty={k.filled_quantity}")
        logger.critical("CRYPTO_BASIS_ORPHAN_RECOVERY_VERIFIED mode=KALSHI_EMERGENCY_HEDGE gemini_no_q=1 kalshi_yes_q=1")
    finally:
        await gemini.close(); await kalshi.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())

if __name__=="__main__": main()

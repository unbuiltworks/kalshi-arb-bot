"""Reconcile one AWS-local Gemini BTC5m canary from authoritative venue history.

This module never places/cancels/sells an order. It reads the unresolved local
reservation, queries Gemini order history and settled positions, and only when the
venue evidence is unambiguous appends corrected local events and clears the local
reservation. Realized P&L is taken from Gemini's settled netProfit field.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
from typing import Any

from src.gemini_prediction_auth import private_rest_request
from src import gemini_short_crypto_local_budget as budget


async def _post(path: str, params: dict[str, Any]) -> dict[str, Any]:
    r = await private_rest_request("POST", path, params=params, timeout=8.0)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini reconcile failed path={path} http={r.status_code}")
    body = r.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"Gemini reconcile malformed path={path}")
    return body


def _rows(body: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = body.get(key)
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


async def reconcile() -> dict[str, Any]:
    local = budget.local_open_state()
    if local is None:
        raise RuntimeError("no unresolved AWS-local canary reservation")
    trade_id = str(local.get("trade_id") or "")
    symbol = str(local.get("symbol") or "")
    event_ticker = str(local.get("event_ticker") or "")
    if not trade_id or not symbol.startswith("GEMI-BTC05M") or not event_ticker.startswith("BTC05M"):
        raise RuntimeError("local reservation is not the expected Gemini BTC5m canary")

    filled_body, cancelled_body, settled_body = await asyncio.gather(
        _post("/v1/prediction-markets/orders/history", {"status": "filled", "symbol": symbol, "limit": 100}),
        _post("/v1/prediction-markets/orders/history", {"status": "cancelled", "symbol": symbol, "limit": 100}),
        _post("/v1/prediction-markets/positions/settled", {"eventTicker": event_ticker, "limit": 100, "offset": 0}),
    )

    filled = [r for r in _rows(filled_body, "orders") if str(r.get("symbol") or symbol) == symbol]
    cancelled = [r for r in _rows(cancelled_body, "orders") if str(r.get("symbol") or symbol) == symbol]
    settled = []
    for r in _rows(settled_body, "positions"):
        rsym = str(r.get("instrumentSymbol") or r.get("symbol") or "")
        if rsym == symbol:
            settled.append(r)

    if len(filled) != 1:
        raise RuntimeError(f"expected exactly one filled order for canary symbol, got {len(filled)}")
    if len(settled) != 1:
        raise RuntimeError(f"expected exactly one settled position for canary symbol, got {len(settled)}")

    order = filled[0]
    position = settled[0]
    filled_qty = D(str(order.get("filledQuantity") or "0"))
    position_qty = D(str(position.get("positionQuantity") or "0"))
    order_outcome = str(order.get("outcome") or "").lower()
    settled_outcome = str(position.get("outcome") or "").lower()
    resolution = str(position.get("resolutionSide") or "").lower()
    cost_basis = D(str(position.get("costBasis") or "0"))
    payout = D(str(position.get("payout") or "0"))
    net_profit = D(str(position.get("netProfit") or "0"))

    if filled_qty != D("1") or position_qty != D("1"):
        raise RuntimeError("unexpected canary quantity during reconciliation")
    if order_outcome not in {"yes", "no"} or settled_outcome != order_outcome:
        raise RuntimeError("filled-order outcome does not match settled position")
    if resolution not in {"yes", "no"}:
        raise RuntimeError("settlement resolution side missing")
    if cost_basis <= 0 or payout not in {D("0"), D("1")}:
        raise RuntimeError("unexpected settlement economics")

    correction = budget._event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="RECONCILIATION_CORRECTION",
        status="ONE_SIDED_FILL_SETTLED",
    )
    correction["event_id"] = budget._safe_id(f"{trade_id}:reconciliation-correction:gemini-settled")
    correction.update({"quantity": "1", "legs_required": 2, "legs_filled": 1})
    correction["metadata"].update({
        "authoritative_source": "GEMINI_SETTLED_POSITION",
        "prior_local_status_incorrect": True,
        "actual_filled_outcome": order_outcome,
        "actual_filled_quantity": str(filled_qty),
        "actual_fill_price": str(order.get("avgExecutionPrice") or order.get("price") or ""),
        "cancelled_order_count": len(cancelled),
        "resolution_side": resolution,
        "cost_basis": str(cost_basis),
        "payout": str(payout),
        "net_profit": str(net_profit),
        "paired_arb": False,
        "client_order_id_collision": True,
    })
    budget._append(correction)

    realized = budget._event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="TRADE_REALIZED",
        status="SETTLED_REALIZED_ONE_SIDED",
    )
    realized["event_id"] = budget._safe_id(f"{trade_id}:trade-realized:gemini-settled")
    realized.update({
        "quantity": "1",
        "amount": str(cost_basis),
        "net_pnl": str(net_profit),
        "legs_required": 2,
        "legs_filled": 1,
    })
    realized["metadata"].update({
        "authoritative_source": "GEMINI_SETTLED_POSITION",
        "realized": True,
        "actual_filled_outcome": order_outcome,
        "resolution_side": resolution,
        "cost_basis": str(cost_basis),
        "payout": str(payout),
        "paired_arb": False,
        "client_order_id_collision": True,
    })
    budget._append(realized)

    released = budget._event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="CAPITAL_RELEASED",
        status="CAPITAL_RELEASED_AFTER_SETTLEMENT",
    )
    released["event_id"] = budget._safe_id(f"{trade_id}:capital-released:settled-reconciliation")
    released.update({"amount": "0", "legs_required": 2, "legs_filled": 1})
    released["metadata"].update({"release_reason": "AUTHORITATIVE_GEMINI_SETTLEMENT_RECONCILED"})
    budget._append(released)
    budget._clear_open()

    return {
        "type": "GEMINI_BTC5M_LOCAL_RECONCILIATION",
        "trade_id": trade_id,
        "symbol": symbol,
        "filled_outcome": order_outcome,
        "filled_quantity": str(filled_qty),
        "cost_basis": str(cost_basis),
        "payout": str(payout),
        "resolution_side": resolution,
        "net_profit": str(net_profit),
        "paired_arb": False,
        "local_reservation_cleared": budget.local_open_state() is None,
        "venue_mutation": False,
    }


async def main() -> None:
    print(json.dumps(await reconcile(), sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

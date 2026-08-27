"""Authoritative settlement reconciliation for one Gemini BTC5m inventory-maker canary.

Venue access is read-only. Local ledger changes occur only after exact client-order
identity, fill quantity/outcome, and Gemini settled-position economics all agree.
This module intentionally does not reuse the older collision-era reconciler.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
from typing import Any, Mapping

from src.gemini_prediction_auth import private_rest_request
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
from src import gemini_short_crypto_inventory_local_budget as budget
from src import gemini_short_crypto_local_budget as local_base

ONE = D("1")


async def _post(path: str, params: dict[str, Any]) -> dict[str, Any]:
    r = await private_rest_request("POST", path, params=params, timeout=8.0)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini inventory reconcile failed path={path} http={r.status_code}")
    body = r.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"Gemini inventory reconcile malformed path={path}")
    return body


def _rows(body: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    raw = body.get(key)
    return [dict(r) for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []


def _client_id(row: Mapping[str, Any]) -> str:
    return str(row.get("clientOrderId") or row.get("client_order_id") or "")


def _outcome(row: Mapping[str, Any]) -> str:
    return str(row.get("outcome") or row.get("eventOutcome") or row.get("event_outcome") or "").strip().lower()


def _filled(row: Mapping[str, Any]) -> D:
    return D(str(row.get("filledQuantity") or row.get("filled_quantity") or "0"))


def _price(row: Mapping[str, Any]) -> str:
    return str(row.get("avgExecutionPrice") or row.get("avg_execution_price") or row.get("price") or "")


def _one_exact(rows: list[dict[str, Any]], client_id: str) -> dict[str, Any]:
    hits = [r for r in rows if _client_id(r) == client_id]
    if len(hits) != 1:
        raise RuntimeError(f"expected exactly one authoritative order row for {client_id}, got {len(hits)}")
    return hits[0]


async def reconcile() -> dict[str, Any]:
    local = local_base.local_open_state()
    if local is None:
        raise RuntimeError("no unresolved AWS-local inventory reservation")
    if str(local.get("strategy") or "") != budget.STRATEGY:
        raise RuntimeError("open reservation is not the inventory-maker strategy")

    trade_id = str(local.get("trade_id") or "")
    symbol = str(local.get("symbol") or "")
    event_ticker = str(local.get("event_ticker") or "")
    if not trade_id or not symbol.startswith("GEMI-BTC05M") or not event_ticker.startswith("BTC05M"):
        raise RuntimeError("unexpected inventory reservation identity")

    yes_id, no_id = maker_client_ids(trade_id)
    filled_body, cancelled_body, settled_body = await asyncio.gather(
        _post("/v1/prediction-markets/orders/history", {"status": "filled", "symbol": symbol, "limit": 100}),
        _post("/v1/prediction-markets/orders/history", {"status": "cancelled", "symbol": symbol, "limit": 100}),
        _post("/v1/prediction-markets/positions/settled", {"eventTicker": event_ticker, "limit": 100, "offset": 0}),
    )

    order_rows = _rows(filled_body, "orders") + _rows(cancelled_body, "orders")
    # A venue may surface the same terminal order in multiple status queries; dedupe
    # exact client IDs by preferring the row with the greater filled quantity.
    expected: dict[str, dict[str, Any]] = {}
    for row in order_rows:
        cid = _client_id(row)
        if cid not in {yes_id, no_id}:
            continue
        prior = expected.get(cid)
        if prior is None or _filled(row) > _filled(prior):
            expected[cid] = row
    if set(expected) != {yes_id, no_id}:
        raise RuntimeError(f"missing exact canary order history expected=2 found={len(expected)}")

    yes_row = expected[yes_id]
    no_row = expected[no_id]
    yes_fill, no_fill = _filled(yes_row), _filled(no_row)
    yes_outcome, no_outcome = _outcome(yes_row), _outcome(no_row)
    if yes_outcome != "yes" or no_outcome != "no":
        raise RuntimeError(
            f"client-id/outcome mismatch yes_id_outcome={yes_outcome!r} no_id_outcome={no_outcome!r}"
        )
    if not (D("0") <= yes_fill <= ONE and D("0") <= no_fill <= ONE):
        raise RuntimeError("inventory fill quantity outside q=1 bounds")
    if (yes_fill > 0) + (no_fill > 0) != 1:
        raise RuntimeError(f"expected exactly one one-sided fill, got yes={yes_fill} no={no_fill}")

    actual_outcome = "yes" if yes_fill > 0 else "no"
    actual_fill = yes_fill if yes_fill > 0 else no_fill
    actual_row = yes_row if yes_fill > 0 else no_row
    if actual_fill != ONE:
        raise RuntimeError(f"expected full q=1 one-sided fill, got {actual_fill}")

    settled_positions = []
    for row in _rows(settled_body, "positions"):
        rsym = str(row.get("instrumentSymbol") or row.get("symbol") or "")
        if rsym == symbol:
            settled_positions.append(row)
    if not settled_positions:
        raise RuntimeError("Gemini settlement not posted yet for inventory canary")

    matches = [r for r in settled_positions if _outcome(r) == actual_outcome]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one settled position matching {actual_outcome}, got {len(matches)}"
        )
    position = matches[0]
    position_qty = D(str(position.get("positionQuantity") or position.get("position_quantity") or "0"))
    resolution = str(position.get("resolutionSide") or position.get("resolution_side") or "").lower()
    cost_basis = D(str(position.get("costBasis") or position.get("cost_basis") or "0"))
    payout = D(str(position.get("payout") or "0"))
    net_profit = D(str(position.get("netProfit") or position.get("net_profit") or "0"))
    if position_qty != ONE:
        raise RuntimeError(f"unexpected settled position quantity {position_qty}")
    if resolution not in {"yes", "no"}:
        raise RuntimeError("settlement resolution side missing")
    if cost_basis <= 0 or payout not in {D("0"), D("1")}:
        raise RuntimeError("unexpected settled position economics")

    correction = budget._event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="RECONCILIATION_CORRECTION",
        status="INVENTORY_ONE_SIDED_FILL_SETTLED",
    )
    correction["event_id"] = local_base._safe_id(f"{trade_id}:inventory-reconcile:settled")
    correction.update({"quantity": "1", "legs_required": 0, "legs_filled": 1})
    correction["metadata"].update({
        "authoritative_source": "GEMINI_EXACT_CLIENT_IDS_PLUS_SETTLED_POSITION",
        "yes_client_order_id": yes_id,
        "no_client_order_id": no_id,
        "yes_order_outcome": yes_outcome,
        "no_order_outcome": no_outcome,
        "yes_fill": str(yes_fill),
        "no_fill": str(no_fill),
        "actual_filled_outcome": actual_outcome,
        "actual_fill_price": _price(actual_row),
        "resolution_side": resolution,
        "cost_basis": str(cost_basis),
        "payout": str(payout),
        "net_profit": str(net_profit),
        "paired_arb": False,
        "client_order_id_collision": False,
    })
    local_base._append(correction)

    realized = budget._event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="TRADE_REALIZED",
        status="SETTLED_REALIZED_ONE_SIDED_INVENTORY",
    )
    realized["event_id"] = local_base._safe_id(f"{trade_id}:inventory-trade-realized:settled")
    realized.update({
        "quantity": "1",
        "amount": str(cost_basis),
        "net_pnl": str(net_profit),
        "legs_required": 0,
        "legs_filled": 1,
    })
    realized["metadata"].update({
        "authoritative_source": "GEMINI_SETTLED_POSITION",
        "realized": True,
        "actual_filled_outcome": actual_outcome,
        "resolution_side": resolution,
        "cost_basis": str(cost_basis),
        "payout": str(payout),
        "paired_arb": False,
    })
    local_base._append(realized)

    await budget.release(
        symbol=symbol,
        event_ticker=event_ticker,
        trade_id=trade_id,
        reason="AUTHORITATIVE_GEMINI_INVENTORY_SETTLEMENT_RECONCILED",
    )

    return {
        "type": "GEMINI_BTC5M_INVENTORY_SETTLEMENT_RECONCILIATION",
        "trade_id": trade_id,
        "symbol": symbol,
        "yes_client_order_id": yes_id,
        "no_client_order_id": no_id,
        "yes_order_outcome": yes_outcome,
        "no_order_outcome": no_outcome,
        "yes_fill": str(yes_fill),
        "no_fill": str(no_fill),
        "filled_outcome": actual_outcome,
        "fill_price": _price(actual_row),
        "cost_basis": str(cost_basis),
        "payout": str(payout),
        "resolution_side": resolution,
        "net_profit": str(net_profit),
        "paired_arb": False,
        "client_order_id_collision": False,
        "local_reservation_cleared": local_base.local_open_state() is None,
        "venue_mutation": False,
    }


async def main() -> None:
    print(json.dumps(await reconcile(), sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

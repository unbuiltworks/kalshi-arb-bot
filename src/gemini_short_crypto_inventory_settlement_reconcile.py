"""Authoritative settlement reconciliation for Gemini BTC5m inventory-maker q=1.

Venue access is read-only. Local ledger changes occur only after exact client-order
identity, fill quantity/outcome, and Gemini settled-position economics all agree.
Supports the bounded strategy's valid one-sided, matched, and fractional fill shapes.
This intentionally does not reuse the older collision-era reconciler.
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
ZERO = D("0")


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


def _position_qty(row: Mapping[str, Any]) -> D:
    return D(str(
        row.get("positionQuantity")
        or row.get("position_quantity")
        or row.get("totalQuantity")
        or row.get("total_quantity")
        or "0"
    ))


def _position_cost(row: Mapping[str, Any]) -> D:
    return D(str(row.get("costBasis") or row.get("cost_basis") or "0"))


def _position_payout(row: Mapping[str, Any]) -> D:
    return D(str(row.get("payout") or "0"))


def _position_net_profit(row: Mapping[str, Any]) -> D:
    return D(str(row.get("netProfit") or row.get("net_profit") or "0"))


def _terminal_exact_rows(
    rows: list[dict[str, Any]], yes_id: str, no_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = _client_id(row)
        if cid not in {yes_id, no_id}:
            continue
        prior = expected.get(cid)
        # A terminal order may appear in more than one status query. Prefer the row
        # carrying the larger authoritative filledQuantity.
        if prior is None or _filled(row) > _filled(prior):
            expected[cid] = row
    if set(expected) != {yes_id, no_id}:
        raise RuntimeError(f"missing exact canary order history expected=2 found={len(expected)}")
    return expected[yes_id], expected[no_id]


def _settled_for_fill(
    settled_positions: list[dict[str, Any]], *, outcome: str, quantity: D
) -> dict[str, Any]:
    matches = [r for r in settled_positions if _outcome(r) == outcome]
    if len(matches) != 1:
        raise RuntimeError(f"expected one settled position matching {outcome}, got {len(matches)}")
    row = matches[0]
    position_qty = _position_qty(row)
    if position_qty != quantity:
        raise RuntimeError(
            f"settled quantity mismatch outcome={outcome} fill={quantity} position={position_qty}"
        )
    cost = _position_cost(row)
    payout = _position_payout(row)
    if cost <= 0:
        raise RuntimeError(f"nonpositive settled cost basis outcome={outcome}")
    if payout not in {ZERO, quantity}:
        raise RuntimeError(
            f"unexpected binary payout outcome={outcome} quantity={quantity} payout={payout}"
        )
    return row


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

    yes_row, no_row = _terminal_exact_rows(
        _rows(filled_body, "orders") + _rows(cancelled_body, "orders"), yes_id, no_id
    )
    yes_fill, no_fill = _filled(yes_row), _filled(no_row)
    yes_outcome, no_outcome = _outcome(yes_row), _outcome(no_row)
    if yes_outcome != "yes" or no_outcome != "no":
        raise RuntimeError(
            f"client-id/outcome mismatch yes_id_outcome={yes_outcome!r} no_id_outcome={no_outcome!r}"
        )
    if not (ZERO <= yes_fill <= ONE and ZERO <= no_fill <= ONE):
        raise RuntimeError("inventory fill quantity outside q=1 bounds")
    if yes_fill == ZERO and no_fill == ZERO:
        raise RuntimeError("open reservation has zero authoritative fills; manual review required")

    settled_positions = []
    for row in _rows(settled_body, "positions"):
        rsym = str(row.get("instrumentSymbol") or row.get("symbol") or "")
        if rsym == symbol:
            settled_positions.append(row)
    if not settled_positions:
        raise RuntimeError("Gemini settlement not posted yet for inventory canary")

    filled: list[tuple[str, D, dict[str, Any]]] = []
    if yes_fill > ZERO:
        filled.append(("yes", yes_fill, yes_row))
    if no_fill > ZERO:
        filled.append(("no", no_fill, no_row))

    settled_by_outcome: dict[str, dict[str, Any]] = {}
    resolutions: set[str] = set()
    total_cost = ZERO
    total_payout = ZERO
    total_net_profit = ZERO
    for outcome, qty, _ in filled:
        position = _settled_for_fill(settled_positions, outcome=outcome, quantity=qty)
        resolution = str(position.get("resolutionSide") or position.get("resolution_side") or "").lower()
        if resolution not in {"yes", "no"}:
            raise RuntimeError("settlement resolution side missing")
        resolutions.add(resolution)
        settled_by_outcome[outcome] = position
        total_cost += _position_cost(position)
        total_payout += _position_payout(position)
        total_net_profit += _position_net_profit(position)
    if len(resolutions) != 1:
        raise RuntimeError(f"inconsistent resolution sides across settled positions: {sorted(resolutions)}")
    resolution = next(iter(resolutions))

    legs_filled = len(filled)
    gross_quantity = yes_fill + no_fill
    matched = yes_fill > ZERO and no_fill > ZERO
    fill_prices = {
        "yes": _price(yes_row) if yes_fill > ZERO else None,
        "no": _price(no_row) if no_fill > ZERO else None,
    }
    settlement_detail = {
        outcome: {
            "quantity": str(qty),
            "cost_basis": str(_position_cost(settled_by_outcome[outcome])),
            "payout": str(_position_payout(settled_by_outcome[outcome])),
            "net_profit": str(_position_net_profit(settled_by_outcome[outcome])),
        }
        for outcome, qty, _ in filled
    }

    correction_status = "INVENTORY_MATCHED_FILL_SETTLED" if matched else "INVENTORY_ONE_SIDED_FILL_SETTLED"
    correction = budget._event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="RECONCILIATION_CORRECTION",
        status=correction_status,
    )
    correction["event_id"] = local_base._safe_id(f"{trade_id}:inventory-reconcile:settled")
    correction.update({"quantity": str(gross_quantity), "legs_required": 0, "legs_filled": legs_filled})
    correction["metadata"].update({
        "authoritative_source": "GEMINI_EXACT_CLIENT_IDS_PLUS_SETTLED_POSITIONS",
        "yes_client_order_id": yes_id,
        "no_client_order_id": no_id,
        "yes_order_outcome": yes_outcome,
        "no_order_outcome": no_outcome,
        "yes_fill": str(yes_fill),
        "no_fill": str(no_fill),
        "fill_prices": fill_prices,
        "resolution_side": resolution,
        "settlement_by_outcome": settlement_detail,
        "cost_basis": str(total_cost),
        "payout": str(total_payout),
        "net_profit": str(total_net_profit),
        "matched_inventory": matched,
        "paired_arb": False,
        "client_order_id_collision": False,
    })
    local_base._append(correction)

    realized_status = "SETTLED_REALIZED_MATCHED_INVENTORY" if matched else "SETTLED_REALIZED_ONE_SIDED_INVENTORY"
    realized = budget._event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="TRADE_REALIZED",
        status=realized_status,
    )
    realized["event_id"] = local_base._safe_id(f"{trade_id}:inventory-trade-realized:settled")
    realized.update({
        "quantity": str(gross_quantity),
        "amount": str(total_cost),
        "net_pnl": str(total_net_profit),
        "legs_required": 0,
        "legs_filled": legs_filled,
    })
    realized["metadata"].update({
        "authoritative_source": "GEMINI_SETTLED_POSITIONS",
        "realized": True,
        "yes_fill": str(yes_fill),
        "no_fill": str(no_fill),
        "resolution_side": resolution,
        "settlement_by_outcome": settlement_detail,
        "cost_basis": str(total_cost),
        "payout": str(total_payout),
        "matched_inventory": matched,
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
        "fill_prices": fill_prices,
        "matched_inventory": matched,
        "settlement_by_outcome": settlement_detail,
        "cost_basis": str(total_cost),
        "payout": str(total_payout),
        "resolution_side": resolution,
        "net_profit": str(total_net_profit),
        "paired_arb": False,
        "client_order_id_collision": False,
        "local_reservation_cleared": local_base.local_open_state() is None,
        "venue_mutation": False,
    }


async def main() -> None:
    print(json.dumps(await reconcile(), sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

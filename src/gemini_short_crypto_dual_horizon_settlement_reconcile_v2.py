"""Authoritative 5m/15m inventory reconciliation from exact fills + event resolution.

The prior reconciler assumed every inventory fill would later appear in Gemini's
`positions/settled` response. A fully matched YES+NO complete set can net away and
produce no matching settled-position row, even though both orders filled and the
event settled. v2 therefore uses:

1. exact distinct client-order IDs from authenticated order history;
2. Gemini's public event record as the authoritative resolution source; and
3. Gemini's published maker-fee formula for the MOC/post-only fills.

The proven 5m ledger is accepted for BTC05M and ETH05M identities. 15m remains
BTC15M-only in this reconciler. Maker rebates/rewards are deliberately excluded
from trading P&L and must be accounted separately from Gemini's incentive payout
APIs.

Venue access is read-only. The only mutation is the AWS-local fsynced ledger and
reservation release after all identity, fill, resolution, and economics checks pass.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
from typing import Any, Mapping

import httpx

from src.gemini_prediction_auth import private_rest_request
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src import gemini_short_crypto_inventory_local_budget as budget5
from src import gemini_short_crypto_inventory15_local_budget as budget15
from src import gemini_short_crypto_local_budget as local_base

ONE = D("1")
ZERO = D("0")
REST = "https://api.gemini.com"


async def _post(path: str, params: dict[str, Any]) -> dict[str, Any]:
    r = await private_rest_request("POST", path, params=params, timeout=8.0)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini dual-horizon reconcile failed path={path} http={r.status_code}")
    body = r.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"Gemini dual-horizon reconcile malformed path={path}")
    return body


async def _event(event_ticker: str) -> dict[str, Any]:
    async with httpx.AsyncClient(
        timeout=8.0,
        follow_redirects=True,
        headers={"User-Agent": "arb-gemini-inventory-reconcile/2.1", "Cache-Control": "no-cache"},
    ) as http:
        r = await http.get(f"{REST}/v1/prediction-markets/events/{event_ticker}")
    if r.status_code != 200:
        raise RuntimeError(f"Gemini event lookup failed ticker={event_ticker} http={r.status_code}")
    body = r.json()
    if not isinstance(body, dict):
        raise RuntimeError("Gemini event lookup malformed")
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


def _price(row: Mapping[str, Any]) -> D:
    raw = row.get("avgExecutionPrice") or row.get("avg_execution_price") or row.get("price")
    if raw in {None, ""}:
        return ZERO
    return D(str(raw))


def _strategy(local: Mapping[str, Any]):
    strategy = str(local.get("strategy") or "")
    symbol = str(local.get("symbol") or "")
    ticker = str(local.get("event_ticker") or "")
    if strategy == budget5.STRATEGY:
        if symbol.startswith("GEMI-BTC05M") and ticker.startswith("BTC05M"):
            return "5m", budget5
        if symbol.startswith("GEMI-ETH05M") and ticker.startswith("ETH05M"):
            return "5m", budget5
    if strategy == budget15.STRATEGY and symbol.startswith("GEMI-BTC15M") and ticker.startswith("BTC15M"):
        return "15m", budget15
    raise RuntimeError(f"unexpected dual-horizon reservation identity strategy={strategy!r} symbol={symbol!r}")


def _terminal_rows(rows: list[dict[str, Any]], yes_id: str, no_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = _client_id(row)
        if cid not in {yes_id, no_id}:
            continue
        prior = expected.get(cid)
        if prior is None or _filled(row) > _filled(prior):
            expected[cid] = row
    if set(expected) != {yes_id, no_id}:
        raise RuntimeError(f"missing exact canary order history expected=2 found={len(expected)}")
    return expected[yes_id], expected[no_id]


def _event_resolution(event: Mapping[str, Any], symbol: str) -> tuple[str, str | None]:
    status = str(event.get("status") or "").strip().lower()
    contracts = event.get("contracts")
    rows = [dict(r) for r in contracts if isinstance(r, Mapping)] if isinstance(contracts, list) else []
    matches = [r for r in rows if str(r.get("instrumentSymbol") or "") == symbol]
    if len(matches) != 1:
        raise RuntimeError(f"Gemini event contract match failed symbol={symbol} count={len(matches)}")
    contract = matches[0]
    contract_status = str(contract.get("status") or "").strip().lower()
    resolution = str(contract.get("resolutionSide") or "").strip().lower()
    resolved_at = contract.get("resolvedAt") or event.get("resolvedAt")
    if status != "settled" or contract_status != "settled" or resolution not in {"yes", "no"}:
        raise RuntimeError("Gemini settlement not posted yet for inventory canary")
    return resolution, None if resolved_at is None else str(resolved_at)


def settled_economics(
    *,
    yes_fill: D,
    no_fill: D,
    yes_price: D,
    no_price: D,
    resolution_side: str,
) -> dict[str, D | str | bool]:
    yes_fill, no_fill = D(str(yes_fill)), D(str(no_fill))
    yes_price, no_price = D(str(yes_price)), D(str(no_price))
    resolution = str(resolution_side).lower()
    if not (ZERO <= yes_fill <= ONE and ZERO <= no_fill <= ONE):
        raise ValueError("inventory fill quantity outside q=1 bounds")
    if resolution not in {"yes", "no"}:
        raise ValueError("invalid resolution side")
    if yes_fill > ZERO and not (ZERO < yes_price < ONE):
        raise ValueError("invalid YES execution price")
    if no_fill > ZERO and not (ZERO < no_price < ONE):
        raise ValueError("invalid NO execution price")

    yes_fee = gemini_predictions_maker_fee(yes_price, yes_fill) if yes_fill > ZERO else ZERO
    no_fee = gemini_predictions_maker_fee(no_price, no_fill) if no_fill > ZERO else ZERO
    raw_cost = yes_price * yes_fill + no_price * no_fill
    maker_fees = yes_fee + no_fee
    payout = yes_fill if resolution == "yes" else no_fill
    net_profit = payout - raw_cost - maker_fees
    matched_qty = min(yes_fill, no_fill)
    residual_qty = abs(yes_fill - no_fill)
    return {
        "raw_cost": raw_cost,
        "yes_maker_fee": yes_fee,
        "no_maker_fee": no_fee,
        "maker_fees": maker_fees,
        "payout": payout,
        "net_profit": net_profit,
        "matched_quantity": matched_qty,
        "residual_quantity": residual_qty,
        "matched_complete_set": matched_qty > ZERO,
        "resolution_side": resolution,
    }


def _history_resolution(row: Mapping[str, Any]) -> str | None:
    metadata = row.get("contractMetadata")
    if not isinstance(metadata, Mapping):
        return None
    value = str(metadata.get("resolutionSide") or "").strip().lower()
    return value if value in {"yes", "no"} else None


async def reconcile() -> dict[str, Any]:
    local = local_base.local_open_state()
    if local is None:
        raise RuntimeError("no unresolved AWS-local inventory reservation")
    horizon, budget = _strategy(local)
    trade_id = str(local.get("trade_id") or "")
    symbol = str(local.get("symbol") or "")
    event_ticker = str(local.get("event_ticker") or "")
    if not trade_id:
        raise RuntimeError("reservation trade id missing")

    yes_id, no_id = maker_client_ids(trade_id)
    filled_body, cancelled_body, event = await asyncio.gather(
        _post("/v1/prediction-markets/orders/history", {"status": "filled", "symbol": symbol, "limit": 100}),
        _post("/v1/prediction-markets/orders/history", {"status": "cancelled", "symbol": symbol, "limit": 100}),
        _event(event_ticker),
    )
    yes_row, no_row = _terminal_rows(
        _rows(filled_body, "orders") + _rows(cancelled_body, "orders"), yes_id, no_id
    )
    yes_fill, no_fill = _filled(yes_row), _filled(no_row)
    yes_outcome, no_outcome = _outcome(yes_row), _outcome(no_row)
    if yes_outcome != "yes" or no_outcome != "no":
        raise RuntimeError(f"client-id/outcome mismatch yes={yes_outcome!r} no={no_outcome!r}")
    if not (ZERO <= yes_fill <= ONE and ZERO <= no_fill <= ONE):
        raise RuntimeError("inventory fill quantity outside q=1 bounds")
    if yes_fill == ZERO and no_fill == ZERO:
        raise RuntimeError("open reservation has zero authoritative fills; manual review required")

    resolution, resolved_at = _event_resolution(event, symbol)
    for row in (yes_row, no_row):
        history_resolution = _history_resolution(row)
        if history_resolution is not None and history_resolution != resolution:
            raise RuntimeError(
                f"order-history/event resolution mismatch history={history_resolution} event={resolution}"
            )

    yes_price = _price(yes_row)
    no_price = _price(no_row)
    economics = settled_economics(
        yes_fill=yes_fill,
        no_fill=no_fill,
        yes_price=yes_price,
        no_price=no_price,
        resolution_side=resolution,
    )
    gross_qty = yes_fill + no_fill
    legs = int(yes_fill > ZERO) + int(no_fill > ZERO)
    matched = bool(economics["matched_complete_set"])
    status = "INVENTORY_MATCHED_FILL_SETTLED" if yes_fill == no_fill and yes_fill > ZERO else "INVENTORY_ONE_SIDED_OR_IMBALANCED_SETTLED"

    common = {
        "authoritative_source": "GEMINI_EXACT_ORDER_HISTORY_PLUS_SETTLED_EVENT_RESOLUTION",
        "horizon": horizon,
        "yes_client_order_id": yes_id,
        "no_client_order_id": no_id,
        "yes_order_outcome": yes_outcome,
        "no_order_outcome": no_outcome,
        "yes_fill": str(yes_fill),
        "no_fill": str(no_fill),
        "yes_price": str(yes_price) if yes_fill > ZERO else None,
        "no_price": str(no_price) if no_fill > ZERO else None,
        "yes_maker_fee": str(economics["yes_maker_fee"]),
        "no_maker_fee": str(economics["no_maker_fee"]),
        "maker_fees": str(economics["maker_fees"]),
        "raw_cost": str(economics["raw_cost"]),
        "payout": str(economics["payout"]),
        "net_profit": str(economics["net_profit"]),
        "resolution_side": resolution,
        "resolved_at": resolved_at,
        "matched_quantity": str(economics["matched_quantity"]),
        "residual_quantity": str(economics["residual_quantity"]),
        "matched_complete_set": matched,
        "maker_rebate_included": False,
        "maker_rebate_accounted_separately": True,
        "normal_sell_exit_used": False,
        "paired_arb": False,
        "client_order_id_collision": False,
    }

    correction = budget._event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="RECONCILIATION_CORRECTION",
        status=status,
    )
    correction["event_id"] = local_base._safe_id(f"{trade_id}:dual-horizon-v2-reconcile:settled")
    correction.update({"quantity": str(gross_qty), "legs_required": 0, "legs_filled": legs})
    correction["metadata"].update(common)
    local_base._append(correction)

    realized = budget._event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="TRADE_REALIZED",
        status="SETTLED_REALIZED_MATCHED_INVENTORY" if yes_fill == no_fill and yes_fill > ZERO else "SETTLED_REALIZED_INVENTORY",
    )
    realized["event_id"] = local_base._safe_id(f"{trade_id}:dual-horizon-v2-trade-realized:settled")
    realized.update({
        "quantity": str(gross_qty),
        "amount": str(economics["raw_cost"] + economics["maker_fees"]),
        "net_pnl": str(economics["net_profit"]),
        "legs_required": 0,
        "legs_filled": legs,
    })
    realized["metadata"].update({**common, "realized": True})
    local_base._append(realized)

    await budget.release(
        symbol=symbol,
        event_ticker=event_ticker,
        trade_id=trade_id,
        reason="AUTHORITATIVE_GEMINI_EVENT_SETTLEMENT_RECONCILED_V2",
    )
    return {
        "type": "GEMINI_SHORT_CRYPTO_INVENTORY_SETTLEMENT_RECONCILIATION_V2",
        "horizon": horizon,
        "trade_id": trade_id,
        "symbol": symbol,
        "yes_client_order_id": yes_id,
        "no_client_order_id": no_id,
        "yes_fill": str(yes_fill),
        "no_fill": str(no_fill),
        "yes_price": str(yes_price) if yes_fill > ZERO else None,
        "no_price": str(no_price) if no_fill > ZERO else None,
        "maker_fees": str(economics["maker_fees"]),
        "raw_cost": str(economics["raw_cost"]),
        "payout": str(economics["payout"]),
        "net_profit": str(economics["net_profit"]),
        "resolution_side": resolution,
        "resolved_at": resolved_at,
        "matched_quantity": str(economics["matched_quantity"]),
        "residual_quantity": str(economics["residual_quantity"]),
        "matched_complete_set": matched,
        "maker_rebate_included": False,
        "maker_rebate_accounted_separately": True,
        "released": True,
        "realized": True,
        "paired_arb": False,
    }


def main() -> None:
    print(json.dumps(asyncio.run(reconcile()), sort_keys=True, default=str))


if __name__ == "__main__":
    main()

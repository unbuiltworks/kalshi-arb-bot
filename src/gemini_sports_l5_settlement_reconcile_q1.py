"""Authoritative settlement reconciler for the Gemini Sports L5 q1 canary.

Uses exact distinct order client IDs plus Gemini's own settled event resolution.
It intentionally does not interpret an external sportsbook rule or infer the
winner: the exact Gemini contract that was traded is the settlement authority.
Maker incentives remain separate and are never included in trading P&L here.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json

from src import gemini_short_crypto_dual_horizon_settlement_reconcile_v2 as core
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
from src import gemini_short_crypto_local_budget as local_base
from src import gemini_sports_l5_inventory_local_budget as budget

ZERO = D("0")


async def reconcile() -> dict:
    local = local_base.local_open_state()
    if local is None:
        raise RuntimeError("no unresolved AWS-local Gemini reservation")
    if str(local.get("strategy") or "") != budget.STRATEGY:
        raise RuntimeError(
            f"unexpected Sports reservation strategy={local.get('strategy')!r}"
        )
    trade_id = str(local.get("trade_id") or "")
    symbol = str(local.get("symbol") or "")
    event_ticker = str(local.get("event_ticker") or "")
    if not trade_id or not symbol or not event_ticker:
        raise RuntimeError("Sports reservation identity incomplete")

    yes_id, no_id = maker_client_ids(trade_id)
    filled_body, cancelled_body, event = await asyncio.gather(
        core._post("/v1/prediction-markets/orders/history", {
            "status": "filled", "symbol": symbol, "limit": 100,
        }),
        core._post("/v1/prediction-markets/orders/history", {
            "status": "cancelled", "symbol": symbol, "limit": 100,
        }),
        core._event(event_ticker),
    )
    yes_row, no_row = core._terminal_rows(
        core._rows(filled_body, "orders") + core._rows(cancelled_body, "orders"),
        yes_id, no_id,
    )
    yes_fill, no_fill = core._filled(yes_row), core._filled(no_row)
    if core._outcome(yes_row) != "yes" or core._outcome(no_row) != "no":
        raise RuntimeError("Sports client-id/outcome mismatch")
    if not (ZERO <= yes_fill <= D("1") and ZERO <= no_fill <= D("1")):
        raise RuntimeError("Sports inventory fill outside q1 bounds")
    if yes_fill == ZERO and no_fill == ZERO:
        raise RuntimeError("open Sports reservation has zero authoritative fills")

    resolution, resolved_at = core._event_resolution(event, symbol)
    for row in (yes_row, no_row):
        history_resolution = core._history_resolution(row)
        if history_resolution is not None and history_resolution != resolution:
            raise RuntimeError("Sports history/event resolution mismatch")

    yes_price, no_price = core._price(yes_row), core._price(no_row)
    economics = core.settled_economics(
        yes_fill=yes_fill, no_fill=no_fill,
        yes_price=yes_price, no_price=no_price,
        resolution_side=resolution,
    )
    gross_qty = yes_fill + no_fill
    legs = int(yes_fill > ZERO) + int(no_fill > ZERO)
    matched = bool(economics["matched_complete_set"])
    status = (
        "SPORTS_INVENTORY_MATCHED_FILL_SETTLED"
        if yes_fill == no_fill and yes_fill > ZERO
        else "SPORTS_INVENTORY_ONE_SIDED_OR_IMBALANCED_SETTLED"
    )
    common = {
        "authoritative_source": "GEMINI_EXACT_ORDER_HISTORY_PLUS_SETTLED_EVENT_RESOLUTION",
        "category": "Sports",
        "yes_client_order_id": yes_id, "no_client_order_id": no_id,
        "yes_fill": str(yes_fill), "no_fill": str(no_fill),
        "yes_price": str(yes_price) if yes_fill > ZERO else None,
        "no_price": str(no_price) if no_fill > ZERO else None,
        "maker_fees": str(economics["maker_fees"]),
        "raw_cost": str(economics["raw_cost"]),
        "payout": str(economics["payout"]),
        "net_profit": str(economics["net_profit"]),
        "resolution_side": resolution, "resolved_at": resolved_at,
        "matched_quantity": str(economics["matched_quantity"]),
        "residual_quantity": str(economics["residual_quantity"]),
        "matched_complete_set": matched,
        "maker_rebate_included": False,
        "liquidity_rewards_included": False,
        "paired_arb": False,
    }

    correction = budget._event_base(
        trade_id=trade_id, symbol=symbol, event_ticker=event_ticker,
        event_type="RECONCILIATION_CORRECTION", status=status,
    )
    correction["event_id"] = local_base._safe_id(
        f"{trade_id}:sports-q1-reconcile:settled"
    )
    correction.update({"quantity": str(gross_qty), "legs_required": 0, "legs_filled": legs})
    correction["metadata"].update(common)
    local_base._append(correction)

    realized = budget._event_base(
        trade_id=trade_id, symbol=symbol, event_ticker=event_ticker,
        event_type="TRADE_REALIZED",
        status=("SETTLED_REALIZED_MATCHED_SPORTS_INVENTORY" if matched
                else "SETTLED_REALIZED_SPORTS_INVENTORY"),
    )
    realized["event_id"] = local_base._safe_id(
        f"{trade_id}:sports-q1-trade-realized:settled"
    )
    realized.update({
        "quantity": str(gross_qty),
        "amount": str(economics["raw_cost"] + economics["maker_fees"]),
        "net_pnl": str(economics["net_profit"]),
        "legs_required": 0, "legs_filled": legs,
    })
    realized["metadata"].update({**common, "realized": True})
    local_base._append(realized)

    await budget.release(
        symbol=symbol, event_ticker=event_ticker,
        trade_id=trade_id,
        reason="AUTHORITATIVE_GEMINI_SPORTS_EVENT_SETTLEMENT_RECONCILED_Q1",
    )
    return {
        "type": "GEMINI_SPORTS_L5_Q1_SETTLEMENT_RECONCILIATION",
        "trade_id": trade_id, "symbol": symbol, "event_ticker": event_ticker,
        "yes_fill": str(yes_fill), "no_fill": str(no_fill),
        "yes_price": str(yes_price) if yes_fill > ZERO else None,
        "no_price": str(no_price) if no_fill > ZERO else None,
        "maker_fees": str(economics["maker_fees"]),
        "raw_cost": str(economics["raw_cost"]),
        "payout": str(economics["payout"]),
        "net_profit": str(economics["net_profit"]),
        "resolution_side": resolution, "resolved_at": resolved_at,
        "matched_quantity": str(economics["matched_quantity"]),
        "residual_quantity": str(economics["residual_quantity"]),
        "matched_complete_set": matched,
        "maker_incentives_included": False,
        "released": True, "realized": True, "paired_arb": False,
    }


def main() -> None:
    print(json.dumps(asyncio.run(reconcile()), sort_keys=True, default=str))


if __name__ == "__main__":
    main()

"""Exact safety cleanup for an interrupted Gemini Sports L5 q1 canary.

Cancels only an ACTIVE order whose deterministic client ID belongs to the durable
Sports reservation, then reads terminal order history without the old 120-second
lookup window. Any filled inventory is retained for authoritative Gemini
settlement. This module never broad-cancels the account and never sells a normal
strategy residual.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
from typing import Any, Mapping

from src.gemini_prediction_auth import private_rest_request
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
from src.gemini_short_crypto_btc5m_inventory_maker_canary_q1 import inventory_status
from src import gemini_short_crypto_local_budget as local
from src import gemini_sports_l5_inventory_local_budget as budget
from src import gemini_short_crypto_dual_horizon_settlement_reconcile_v2 as core


async def _post(path: str, params: dict[str, Any]) -> dict[str, Any]:
    response = await private_rest_request("POST", path, params=params, timeout=8.0)
    if response.status_code != 200:
        raise RuntimeError(f"Sports interrupt cleanup failed path={path} http={response.status_code}")
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"Sports interrupt cleanup malformed path={path}")
    return body


def _rows(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = body.get("orders")
    return [dict(row) for row in raw if isinstance(row, Mapping)] if isinstance(raw, list) else []


def _client_id(row: Mapping[str, Any]) -> str:
    return str(row.get("clientOrderId") or row.get("client_order_id") or "")


def _order_id(row: Mapping[str, Any]) -> str:
    value = row.get("orderId") or row.get("order_id") or row.get("id")
    return "" if value in (None, "") else str(value)


def _best(rows: list[dict[str, Any]], client_id: str) -> dict[str, Any] | None:
    matches = [row for row in rows if _client_id(row) == client_id]
    if not matches:
        return None
    return max(matches, key=lambda row: core._filled(row))


async def _snapshot(symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active_body, filled_body, cancelled_body = await asyncio.gather(
        _post("/v1/prediction-markets/orders/active", {
            "symbol": symbol, "limit": 100, "offset": 0,
        }),
        _post("/v1/prediction-markets/orders/history", {
            "status": "filled", "symbol": symbol, "limit": 100,
        }),
        _post("/v1/prediction-markets/orders/history", {
            "status": "cancelled", "symbol": symbol, "limit": 100,
        }),
    )
    return _rows(active_body), _rows(filled_body) + _rows(cancelled_body)


async def _cancel_exact_active(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"ok": True, "method": "ALREADY_NOT_ACTIVE", "mutation": False}
    order_id = _order_id(row)
    if not order_id:
        raise RuntimeError("active exact canary order missing orderId")
    response = await private_rest_request(
        "POST",
        "/v1/prediction-markets/order/cancel",
        params={"orderId": int(order_id) if order_id.isdigit() else order_id},
        timeout=8.0,
    )
    return {
        "ok": response.status_code == 200,
        "method": "REST_CANCEL_EXACT_ACTIVE_ORDER",
        "http": response.status_code,
        "mutation": True,
    }


async def run() -> dict[str, Any]:
    state = local.local_open_state()
    if state is None:
        return {
            "type": "SPORTS_INTERRUPT_RECONCILE",
            "status": "NO_LOCAL_RESERVATION",
            "order_entry": False,
        }
    if str(state.get("strategy") or "") != budget.STRATEGY:
        raise RuntimeError(f"unexpected reservation strategy={state.get('strategy')!r}")

    trade_id = str(state.get("trade_id") or "")
    symbol = str(state.get("symbol") or "")
    event_ticker = str(state.get("event_ticker") or "")
    if not trade_id or not symbol or not event_ticker:
        raise RuntimeError("incomplete Sports reservation identity")
    yes_id, no_id = maker_client_ids(trade_id)

    active_before, terminal_before = await _snapshot(symbol)
    active_yes = _best(active_before, yes_id)
    active_no = _best(active_before, no_id)

    # Mutation is exact-ID scoped: only cancel a currently active row for either
    # deterministic canary client ID. Never use cancel_session/account-wide cancel.
    yes_cancel, no_cancel = await asyncio.gather(
        _cancel_exact_active(active_yes),
        _cancel_exact_active(active_no),
    )
    if not yes_cancel.get("ok") or not no_cancel.get("ok"):
        raise RuntimeError(
            f"exact canary cancel failed yes={yes_cancel.get('http')} no={no_cancel.get('http')}"
        )

    await asyncio.sleep(0.35)
    active_after, terminal_after = await _snapshot(symbol)
    still_active = [
        row for row in active_after if _client_id(row) in {yes_id, no_id}
    ]
    if still_active:
        raise RuntimeError(f"exact canary order still active count={len(still_active)}")

    all_terminal = terminal_before + terminal_after
    yes_row, no_row = core._terminal_rows(all_terminal, yes_id, no_id)
    yes_fill, no_fill = core._filled(yes_row), core._filled(no_row)
    yes_outcome, no_outcome = core._outcome(yes_row), core._outcome(no_row)
    if yes_outcome != "yes" or no_outcome != "no":
        raise RuntimeError(
            f"Sports client-id/outcome mismatch yes={yes_outcome!r} no={no_outcome!r}"
        )
    if not (D("0") <= yes_fill <= D("1") and D("0") <= no_fill <= D("1")):
        raise RuntimeError("q1 fill bounds violated")

    status = inventory_status(yes_fill, no_fill)
    if yes_fill == 0 and no_fill == 0:
        await budget.release(
            symbol=symbol,
            event_ticker=event_ticker,
            trade_id=trade_id,
            reason="INTERRUPTED_CANARY_ZERO_FILL_EXACT_CANCELLED",
        )
        released = True
    else:
        await budget.result(
            symbol=symbol,
            event_ticker=event_ticker,
            trade_id=trade_id,
            status=status,
            yes_fill=str(yes_fill),
            no_fill=str(no_fill),
            metadata={
                "interrupt_reconciled": True,
                "yes_cancel": yes_cancel,
                "no_cancel": no_cancel,
                "settlement_required": True,
                "normal_sell_exit": False,
                "authoritative_order_source": "ACTIVE_PLUS_UNBOUNDED_TERMINAL_HISTORY",
            },
        )
        released = False

    return {
        "type": "SPORTS_INTERRUPT_RECONCILE",
        "status": status,
        "trade_id": trade_id,
        "symbol": symbol,
        "event_ticker": event_ticker,
        "yes_client_id": yes_id,
        "no_client_id": no_id,
        "yes_fill": str(yes_fill),
        "no_fill": str(no_fill),
        "yes_cancel": yes_cancel,
        "no_cancel": no_cancel,
        "exact_canary_orders_active_after": 0,
        "reservation_released": released,
        "settlement_required": not released,
        "realized": False,
        "order_entry": False,
    }


def main() -> None:
    print(json.dumps(asyncio.run(run()), sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()

"""Release exactly the stale settled Gemini Sports L5 reservation after proof.

This script never mutates Gemini. It clears only the known durable local ledger
reservation after proving the account is flat and the exact contract is settled.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal as D

import httpx

from src.gemini_prediction_auth import private_rest_request
from src import gemini_short_crypto_local_budget as local
from src import gemini_sports_l5_inventory_local_budget as budget

EXPECT_SYMBOL = "GEMI-TENNIS-ATPUSO-20260830-SHA2-TRU-SHA2"
EXPECT_EVENT = "TENNIS-ATPUSO-20260830-SHA2-TRU"
EXPECT_STATUS = "ONE_SIDED_OR_IMBALANCED_INVENTORY_NOT_SETTLED"


async def _post(path: str, params: dict) -> dict:
    response = await private_rest_request("POST", path, params=params, timeout=8.0)
    if response.status_code != 200:
        raise RuntimeError(f"account proof failed path={path} http={response.status_code}")
    body = response.json()
    return body if isinstance(body, dict) else {}


def _rows(body: dict, key: str) -> list[dict]:
    raw = body.get(key) if isinstance(body, dict) else None
    return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []


async def run() -> dict:
    state = local.local_open_state()
    if state is None:
        return {
            "type": "SPORTS_SETTLED_RESERVATION_RELEASE",
            "status": "NO_LOCAL_RESERVATION",
            "released": False,
            "mutation": False,
        }
    if str(state.get("strategy") or "") != budget.STRATEGY:
        raise RuntimeError(f"unexpected reservation strategy={state.get('strategy')!r}")
    if str(state.get("symbol") or "") != EXPECT_SYMBOL or str(state.get("event_ticker") or "") != EXPECT_EVENT:
        raise RuntimeError("refusing non-target Sports reservation")
    if str(state.get("status") or "") != EXPECT_STATUS:
        raise RuntimeError(f"unexpected target reservation status={state.get('status')!r}")
    if D(str(state.get("yes_fill") or "0")) != D("1") or D(str(state.get("no_fill") or "0")) != D("0"):
        raise RuntimeError("unexpected target fill shape")

    active, positions = await asyncio.gather(
        _post("/v1/prediction-markets/orders/active", {"limit": 1000, "offset": 0}),
        _post("/v1/prediction-markets/positions", {"limit": 1000, "offset": 0, "sort": "+expiryDate"}),
    )
    active_rows = _rows(active, "orders")
    nonzero = []
    for row in _rows(positions, "positions"):
        try:
            quantity = D(str(row.get("totalQuantity") or "0"))
        except Exception:
            quantity = D("0")
        if quantity != 0:
            nonzero.append({"symbol": str(row.get("symbol") or ""), "qty": str(quantity)})
    if active_rows or nonzero:
        raise RuntimeError(f"account not flat active={len(active_rows)} nonzero={nonzero}")

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        response = await client.get(f"https://api.gemini.com/v1/prediction-markets/events/{EXPECT_EVENT}")
    if response.status_code != 200:
        raise RuntimeError(f"event read failed http={response.status_code}")
    event = response.json()
    contracts = event.get("contracts") if isinstance(event, dict) else None
    contracts = contracts if isinstance(contracts, list) else []
    matches = [
        contract for contract in contracts
        if isinstance(contract, dict) and str(contract.get("instrumentSymbol") or "") == EXPECT_SYMBOL
    ]
    if len(matches) != 1:
        raise RuntimeError(f"target contract match count={len(matches)}")
    contract = matches[0]
    market_status = str(contract.get("status") or contract.get("marketState") or "").lower()
    resolution = str(contract.get("resolutionSide") or "").lower()
    if market_status != "settled" or resolution not in {"yes", "no"}:
        raise RuntimeError(f"target residual not settled status={market_status!r} resolution={resolution!r}")

    trade_id = str(state.get("trade_id") or "")
    if not trade_id:
        raise RuntimeError("target reservation missing trade_id")
    await budget.release(
        symbol=EXPECT_SYMBOL,
        event_ticker=EXPECT_EVENT,
        trade_id=trade_id,
        reason=f"SETTLED_RESIDUAL_CONFIRMED_{resolution.upper()}_ACCOUNT_FLAT",
    )
    if local.local_open_state() is not None:
        raise RuntimeError("local reservation still open after exact release")
    return {
        "type": "SPORTS_SETTLED_RESERVATION_RELEASE",
        "status": "RELEASED",
        "symbol": EXPECT_SYMBOL,
        "event_ticker": EXPECT_EVENT,
        "resolution": resolution,
        "active_prediction_orders": 0,
        "nonzero_prediction_positions": [],
        "released": True,
        "venue_mutation": False,
        "local_ledger_mutation": True,
        "order_entry": False,
    }


def main() -> None:
    print(json.dumps(asyncio.run(run()), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

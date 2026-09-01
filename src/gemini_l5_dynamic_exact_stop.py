"""Exact-ID shutdown/reconciliation for the Gemini L5 dynamic q1 owner.

The outer shell stops the Docker owner before running this module, so it cannot
submit another cycle while shutdown is being reconciled.  This module then:
- reads the newest durable cycle file;
- derives only that cycle's exact client IDs;
- cancels only matching active Gemini orders by orderId;
- reads terminal history for those IDs;
- refuses an upgrade if any exact-cycle fill, any prediction position, any
  remaining exact order, or any unknown active prediction order exists.

There is deliberately no account/session-wide cancel fallback.
"""
from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping

from src.gemini_prediction_auth import private_rest_request

ZERO = D("0")
STATE_DIR_ENV = "GEMINI_L5_DYNAMIC_STATE_DIR"


def _rows(body: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(body, Mapping):
        return []
    raw = body.get(key)
    return [dict(row) for row in raw if isinstance(row, Mapping)] if isinstance(raw, list) else []


def _client(row: Mapping[str, Any]) -> str:
    return str(row.get("clientOrderId") or row.get("client_order_id") or "")


def _order_id(row: Mapping[str, Any]) -> str:
    value = row.get("orderId") or row.get("order_id") or row.get("id")
    return "" if value in (None, "") else str(value)


def _filled(row: Mapping[str, Any] | None) -> D:
    if not isinstance(row, Mapping):
        return ZERO
    return D(str(row.get("filledQuantity") or row.get("filled_quantity") or "0"))


def newest_cycle(state_dir: Path) -> tuple[Path, dict[str, Any]] | None:
    files = sorted(state_dir.glob("cycle-*.json"))
    if not files:
        return None
    path = files[-1]
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"latest cycle state unreadable path={path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("latest cycle state is not an object")
    return path, payload


def cycle_client_ids(payload: Mapping[str, Any]) -> set[str]:
    orders = payload.get("orders")
    if not isinstance(orders, list):
        return set()
    return {
        str(row.get("id") or "")
        for row in orders
        if isinstance(row, Mapping) and str(row.get("id") or "")
    }


async def _post(path: str, params: dict[str, Any]) -> dict[str, Any]:
    response = await private_rest_request("POST", path, params=params, timeout=8.0)
    if response.status_code != 200:
        raise RuntimeError(f"shutdown read/mutation failed path={path} http={response.status_code}")
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"shutdown malformed response path={path}")
    return body


async def _active() -> list[dict[str, Any]]:
    return _rows(await _post("/v1/prediction-markets/orders/active", {"limit": 1000, "offset": 0}), "orders")


async def _positions() -> list[dict[str, Any]]:
    return _rows(
        await _post("/v1/prediction-markets/positions", {"limit": 1000, "offset": 0, "sort": "+expiryDate"}),
        "positions",
    )


async def _terminal(status: str) -> list[dict[str, Any]]:
    return _rows(
        await _post("/v1/prediction-markets/orders/history", {"status": status, "limit": 1000}),
        "orders",
    )


async def _cancel_exact(row: Mapping[str, Any]) -> dict[str, Any]:
    order_id = _order_id(row)
    if not order_id:
        raise RuntimeError(f"exact dynamic active order missing orderId client={_client(row)}")
    response = await private_rest_request(
        "POST",
        "/v1/prediction-markets/order/cancel",
        params={"orderId": int(order_id) if order_id.isdigit() else order_id},
        timeout=8.0,
    )
    if response.status_code != 200:
        raise RuntimeError(f"exact dynamic cancel failed client={_client(row)} http={response.status_code}")
    return {"client": _client(row), "order_id": order_id, "http": response.status_code}


async def run() -> dict[str, Any]:
    state_dir = Path(os.getenv(STATE_DIR_ENV, "/state"))
    latest = newest_cycle(state_dir)
    if latest is None:
        # No durable mutation cycle ever existed.  Still require the account to
        # be completely flat before declaring upgrade-safe.
        ids: set[str] = set()
        cycle_path = None
        cycle_terminal = None
    else:
        path, payload = latest
        ids = cycle_client_ids(payload)
        cycle_path = str(path)
        cycle_terminal = bool(payload.get("terminal"))
        if not ids:
            raise RuntimeError(f"latest dynamic cycle has no exact client IDs path={path}")

    active_before = await _active()
    exact_active = [row for row in active_before if _client(row) in ids]
    unknown_active = [row for row in active_before if _client(row) not in ids]
    if unknown_active:
        raise RuntimeError(
            "unknown active prediction orders prevent dynamic upgrade: "
            + json.dumps([{"client": _client(r), "order_id": _order_id(r)} for r in unknown_active], sort_keys=True)
        )

    cancels = []
    for row in exact_active:
        cancels.append(await _cancel_exact(row))
    if exact_active:
        await asyncio.sleep(0.45)

    active_after, filled_rows, cancelled_rows, positions = await asyncio.gather(
        _active(), _terminal("filled"), _terminal("cancelled"), _positions()
    )
    remaining_exact = [row for row in active_after if _client(row) in ids]
    remaining_unknown = [row for row in active_after if _client(row) not in ids]
    if remaining_exact:
        raise RuntimeError(f"exact dynamic orders still active after stop count={len(remaining_exact)}")
    if remaining_unknown:
        raise RuntimeError(f"unknown active prediction orders appeared during stop count={len(remaining_unknown)}")

    best: dict[str, dict[str, Any]] = {}
    for row in filled_rows + cancelled_rows:
        client = _client(row)
        if client not in ids:
            continue
        prior = best.get(client)
        if prior is None or _filled(row) >= _filled(prior):
            best[client] = row
    fills = {client: str(_filled(best.get(client))) for client in sorted(ids)}
    any_fill = any(D(value) > ZERO for value in fills.values())

    nonzero_positions = []
    for row in positions:
        qty = D(str(row.get("totalQuantity") or "0"))
        if qty > ZERO:
            nonzero_positions.append(
                {
                    "symbol": str(row.get("symbol") or ""),
                    "outcome": str(row.get("eventOutcome") or row.get("outcome") or ""),
                    "qty": str(qty),
                }
            )

    result = {
        "type": "GEMINI_L5_DYNAMIC_EXACT_STOP",
        "cycle_path": cycle_path,
        "cycle_terminal_before_stop": cycle_terminal,
        "exact_client_ids": sorted(ids),
        "exact_active_before": len(exact_active),
        "exact_cancels": cancels,
        "fills": fills,
        "nonzero_positions": nonzero_positions,
        "active_orders_after": 0,
        "upgrade_safe": (not any_fill and not nonzero_positions),
        "order_entry": False,
    }
    if any_fill:
        raise RuntimeError("exact dynamic cycle has authoritative fill; refuse upgrade until settlement")
    if nonzero_positions:
        raise RuntimeError("prediction position remains after dynamic stop; refuse upgrade")
    return result


def main() -> None:
    print(json.dumps(asyncio.run(run()), sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()

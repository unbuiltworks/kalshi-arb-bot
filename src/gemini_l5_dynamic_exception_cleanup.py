"""Fail-closed exact-ID cleanup for unexpected Gemini L5 dynamic exceptions.

This is deliberately narrow: it reads only the current durable cycle IDs, cancels
only matching active orders, verifies those exact IDs are absent, and records the
cleanup in the same cycle file. It never issues an account-wide cancel.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Mapping

from src.gemini_prediction_auth import private_rest_request


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


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"exception cleanup state unreadable path={path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("exception cleanup state is not an object")
    return payload


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".cleanup.tmp")
    with tmp.open("w") as handle:
        json.dump(dict(payload), handle, sort_keys=True, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def cycle_ids(payload: Mapping[str, Any]) -> set[str]:
    orders = payload.get("orders")
    if not isinstance(orders, list):
        return set()
    return {
        str(row.get("id") or "")
        for row in orders
        if isinstance(row, Mapping) and str(row.get("id") or "")
    }


async def _active(symbol: str) -> list[dict[str, Any]]:
    response = await private_rest_request(
        "POST",
        "/v1/prediction-markets/orders/active",
        params={"symbol": symbol, "limit": 100, "offset": 0},
        timeout=8.0,
    )
    if response.status_code != 200:
        raise RuntimeError(f"exception cleanup active read failed http={response.status_code}")
    return _rows(response.json(), "orders")


async def _cancel(row: Mapping[str, Any]) -> dict[str, Any]:
    oid = _order_id(row)
    if not oid:
        raise RuntimeError(f"exception cleanup exact order missing orderId client={_client(row)}")
    response = await private_rest_request(
        "POST",
        "/v1/prediction-markets/order/cancel",
        params={"orderId": int(oid) if oid.isdigit() else oid},
        timeout=8.0,
    )
    if response.status_code != 200:
        raise RuntimeError(f"exception cleanup exact cancel failed client={_client(row)} http={response.status_code}")
    return {"client": _client(row), "order_id": oid, "http": response.status_code}


async def cleanup_cycle(path: Path, *, error: str) -> dict[str, Any]:
    payload = _load(path)
    if payload is None:
        return {
            "type": "GEMINI_L5_DYNAMIC_EXCEPTION_CLEANUP",
            "cycle_state_present": False,
            "exact_client_ids": [],
            "cancelled": [],
            "active_exact_after": [],
            "cleanup_verified": True,
        }

    ids = cycle_ids(payload)
    symbol = str(payload.get("symbol") or "").strip()
    if not ids:
        # No durable order IDs means mutation never got far enough to create a
        # captured order set. Mark the failed cycle, but do not invent cancels.
        payload.update({"terminal": True, "status": "EXCEPTION_BEFORE_CAPTURED_ORDERS", "error": error})
        _write(path, payload)
        return {
            "type": "GEMINI_L5_DYNAMIC_EXCEPTION_CLEANUP",
            "cycle_state_present": True,
            "exact_client_ids": [],
            "cancelled": [],
            "active_exact_after": [],
            "cleanup_verified": True,
        }
    if not symbol:
        raise RuntimeError("exception cleanup cycle has exact IDs but no symbol")

    active_before = await _active(symbol)
    exact = [row for row in active_before if _client(row) in ids]
    unknown = [row for row in active_before if _client(row) not in ids]
    if unknown:
        raise RuntimeError(
            "exception cleanup found unknown active prediction orders: "
            + json.dumps([{"client": _client(r), "order_id": _order_id(r)} for r in unknown], sort_keys=True)
        )

    cancelled = []
    for row in exact:
        cancelled.append(await _cancel(row))
    if exact:
        await asyncio.sleep(0.35)

    active_after = await _active(symbol)
    remaining_exact = sorted({_client(row) for row in active_after if _client(row) in ids})
    remaining_unknown = [row for row in active_after if _client(row) not in ids]
    if remaining_unknown:
        raise RuntimeError("unknown active prediction order appeared during exception cleanup")
    if remaining_exact:
        raise RuntimeError(f"exact orders remain active after exception cleanup ids={remaining_exact}")

    result = {
        "type": "GEMINI_L5_DYNAMIC_EXCEPTION_CLEANUP",
        "cycle_state_present": True,
        "exact_client_ids": sorted(ids),
        "cancelled": cancelled,
        "active_exact_after": [],
        "cleanup_verified": True,
    }
    payload.update({
        "terminal": True,
        "status": "EXCEPTION_CLEANUP_VERIFIED",
        "error": error,
        "exception_cleanup": result,
    })
    _write(path, payload)
    return result

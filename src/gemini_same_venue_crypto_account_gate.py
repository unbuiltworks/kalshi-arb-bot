"""Account-authoritative startup gate for same-Gemini q=1 maker execution.

The Command Center reservation ledger is a risk gate, but it is not allowed to be the
only evidence that no exchange order survives a process crash. Before the live maker
loop starts, inspect Gemini active orders and recent order history for our deterministic
same-venue client-order IDs. Cancel zero-fill orphan makers, fail closed on any
unhedged maker fill, and verify no orphan maker remains active.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import time
from typing import Any, Mapping

from src import gemini_same_venue_crypto_maker_q1_wsfirst as live
from src.gemini_prediction_auth import private_rest_request

D = Decimal
PREFIX = "crypto-gem-ladder-"
MAKER_SUFFIX = "-mk"
HEDGE_SUFFIX = "-hg"


def _client(row: Mapping[str, Any]) -> str:
    return str(row.get("clientOrderId") or "")


def _qty(row: Mapping[str, Any]) -> D:
    try:
        return max(D("0"), D(str(row.get("filledQuantity") or "0")))
    except Exception:
        return D("0")


def _is_our_maker(row: Mapping[str, Any]) -> bool:
    cid = _client(row)
    return cid.startswith(PREFIX) and cid.endswith(MAKER_SUFFIX)


async def _post(path: str, params: dict[str, Any]) -> dict[str, Any] | None:
    errors: list[str] = []
    for attempt in range(live.RECOVERY_ATTEMPTS):
        try:
            response = await private_rest_request("POST", path, params=params, timeout=live.RECOVERY_TIMEOUT)
            if response.status_code == 200:
                body = response.json()
                return dict(body) if isinstance(body, Mapping) else None
            errors.append(f"HTTP{response.status_code}")
        except Exception as exc:
            errors.append(type(exc).__name__)
        if attempt + 1 < live.RECOVERY_ATTEMPTS:
            await asyncio.sleep(min(0.8, 0.15 * (2 ** attempt)))
    live.emit("ACCOUNT_GATE_RECOVERY_FAILED", path=path, errors=errors[-12:], order_entry=False, realized=False)
    return None


async def _active() -> list[dict[str, Any]] | None:
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        body = await _post("/v1/prediction-markets/orders/active", {"limit": 100, "offset": offset})
        if body is None:
            return None
        rows = body.get("orders", [])
        if not isinstance(rows, list):
            return None
        page = [dict(x) for x in rows if isinstance(x, Mapping)]
        out.extend(page)
        if len(page) < 100:
            break
        offset += len(page)
        if offset >= 1000:
            break
    return out


async def _history(status: str) -> list[dict[str, Any]] | None:
    now_ms = int(time.time() * 1000)
    body = await _post("/v1/prediction-markets/orders/history", {
        "status": status, "limit": 1000, "from": now_ms-12*3600*1000, "to": now_ms+1000,
    })
    if body is None:
        return None
    rows = body.get("orders", [])
    return [dict(x) for x in rows if isinstance(x, Mapping)] if isinstance(rows, list) else None


async def account_gate() -> bool:
    active = await _active()
    filled = await _history("filled")
    cancelled = await _history("cancelled")
    if active is None or filled is None or cancelled is None:
        live.emit("ACCOUNT_GATE_AMBIGUOUS_HARD_HALT", reason="authoritative_snapshot_failed", order_entry=False, realized=False)
        return False

    recent = filled + cancelled
    filled_by_client = {_client(r): r for r in filled if _client(r)}
    unsafe_fills: list[dict[str, str]] = []
    for row in recent:
        if not _is_our_maker(row) or _qty(row) <= 0:
            continue
        maker_cid = _client(row)
        trade_id = maker_cid[:-len(MAKER_SUFFIX)]
        hedge_cid = f"{trade_id}{HEDGE_SUFFIX}"[:36]
        hedge = filled_by_client.get(hedge_cid)
        if hedge is None or _qty(hedge) < D("1"):
            unsafe_fills.append({
                "maker_client_id": maker_cid,
                "maker_symbol": str(row.get("symbol") or ""),
                "maker_filled": str(_qty(row)),
                "expected_hedge_client_id": hedge_cid,
            })

    our_active = [r for r in active if _is_our_maker(r)]
    active_fills = [r for r in our_active if _qty(r) > 0]
    for row in our_active:
        oid = str(row.get("orderId") or "")
        if not oid or not await live._rest_cancel(oid):
            live.emit("ACCOUNT_GATE_CANCEL_FAILED_HARD_HALT", client_id=_client(row), order_id=oid,
                      filled_quantity=str(_qty(row)), order_entry=False, realized=False)
            return False
        live.emit("ACCOUNT_GATE_ORPHAN_CANCELED", client_id=_client(row), order_id=oid,
                  symbol=str(row.get("symbol") or ""), filled_quantity=str(_qty(row)), order_entry=False, realized=False)

    verify = await _active()
    if verify is None:
        live.emit("ACCOUNT_GATE_VERIFY_AMBIGUOUS_HARD_HALT", order_entry=False, realized=False)
        return False
    remaining = [_client(r) for r in verify if _is_our_maker(r)]
    if remaining:
        live.emit("ACCOUNT_GATE_ORPHAN_REMAINS_HARD_HALT", clients=remaining, order_entry=False, realized=False)
        return False

    if active_fills:
        unsafe_fills.extend({
            "maker_client_id": _client(r), "maker_symbol": str(r.get("symbol") or ""),
            "maker_filled": str(_qty(r)), "expected_hedge_client_id": f"{_client(r)[:-len(MAKER_SUFFIX)]}{HEDGE_SUFFIX}"[:36],
        } for r in active_fills)
    if unsafe_fills:
        live.emit("ACCOUNT_GATE_UNHEDGED_FILL_HARD_HALT", fills=unsafe_fills, order_entry=False, realized=False)
        return False

    live.emit("ACCOUNT_GATE_CLEAR", active_orders_seen=len(active), recent_filled_seen=len(filled),
              recent_cancelled_seen=len(cancelled), orphan_makers_canceled=len(our_active),
              order_entry=False, realized=False)
    return True


async def run() -> dict[str, Any]:
    if live.base.live_armed():
        try:
            clear = await account_gate()
        except Exception as exc:
            live.emit("ACCOUNT_GATE_EXCEPTION_HARD_HALT", error=f"{type(exc).__name__}:{str(exc)[:180]}",
                      order_entry=False, realized=False)
            return {"status": "ACCOUNT_GATE_EXCEPTION_HARD_HALT", "order_entry": False, "realized": False}
        if not clear:
            return {"status": "ACCOUNT_GATE_HARD_HALT", "order_entry": False, "realized": False}
    return await live.run()


if __name__ == "__main__":
    asyncio.run(run())

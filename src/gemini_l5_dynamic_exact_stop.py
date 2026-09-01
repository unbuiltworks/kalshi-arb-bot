"""Exact-ID shutdown/reconciliation for the Gemini L5 dynamic q1 owner.

The outer shell freezes the Docker owner before this module runs. This module
binds itself to the current durable outer session ID, cancels only that session's
newest cycle exact client IDs, and refuses an upgrade while any order or prediction
position remains. Historical cycle files from prior sessions are never eligible
for cancellation merely because their filename has a larger cycle number.

If the current exact cycle filled, an upgrade is permitted only after Gemini's
public event record independently shows the contract SETTLED with a YES/NO
resolution and the private account is flat. There is deliberately no account-wide
or session-wide cancel fallback.
"""
from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping

import httpx

from src.gemini_prediction_auth import private_rest_request

ZERO = D("0")
STATE_DIR_ENV = "GEMINI_L5_DYNAMIC_STATE_DIR"
SESSION_FILE = "session.json"
REST = "https://api.gemini.com"


def _rows(body: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(body, Mapping): return []
    raw = body.get(key)
    return [dict(row) for row in raw if isinstance(row, Mapping)] if isinstance(raw, list) else []


def _client(row: Mapping[str, Any]) -> str:
    return str(row.get("clientOrderId") or row.get("client_order_id") or "")


def _order_id(row: Mapping[str, Any]) -> str:
    value=row.get("orderId") or row.get("order_id") or row.get("id")
    return "" if value in (None,"") else str(value)


def _filled(row: Mapping[str, Any] | None) -> D:
    if not isinstance(row,Mapping): return ZERO
    return D(str(row.get("filledQuantity") or row.get("filled_quantity") or "0"))


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload=json.loads(path.read_text())
    except (OSError,json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} state unreadable path={path}") from exc
    if not isinstance(payload,dict):
        raise RuntimeError(f"{label} state is not an object path={path}")
    return payload


def current_session_id(state_dir: Path) -> str | None:
    path=state_dir/SESSION_FILE
    if not path.exists():
        return None
    payload=_load_json(path,label="outer session")
    sid=str(payload.get("sid") or "").strip()
    if not sid:
        raise RuntimeError(f"outer session state missing sid path={path}")
    return sid


def newest_cycle(
    state_dir: Path,
    *,
    session_id: str | None = None,
) -> tuple[Path, dict[str, Any]] | None:
    """Return newest durable cycle, optionally restricted to one outer session."""
    matches: list[tuple[Path,dict[str,Any]]] = []
    prefix = None if session_id is None else f"{session_id}-c"
    for path in sorted(state_dir.glob("cycle-*.json")):
        payload=_load_json(path,label="cycle")
        if prefix is not None and not str(payload.get("sid") or "").startswith(prefix):
            continue
        matches.append((path,payload))
    return matches[-1] if matches else None


def cycle_client_ids(payload: Mapping[str, Any]) -> set[str]:
    orders=payload.get("orders")
    if not isinstance(orders,list): return set()
    return {str(row.get("id") or "") for row in orders if isinstance(row,Mapping) and str(row.get("id") or "")}


async def _post(path: str, params: dict[str, Any]) -> dict[str, Any]:
    response=await private_rest_request("POST",path,params=params,timeout=8.0)
    if response.status_code!=200: raise RuntimeError(f"shutdown read/mutation failed path={path} http={response.status_code}")
    body=response.json()
    if not isinstance(body,dict): raise RuntimeError(f"shutdown malformed response path={path}")
    return body


async def _active() -> list[dict[str, Any]]:
    return _rows(await _post("/v1/prediction-markets/orders/active",{"limit":1000,"offset":0}),"orders")


async def _positions() -> list[dict[str, Any]]:
    return _rows(await _post("/v1/prediction-markets/positions",{"limit":1000,"offset":0,"sort":"+expiryDate"}),"positions")


async def _terminal(status: str) -> list[dict[str, Any]]:
    return _rows(await _post("/v1/prediction-markets/orders/history",{"status":status,"limit":1000}),"orders")


async def _cancel_exact(row: Mapping[str, Any]) -> dict[str, Any]:
    order_id=_order_id(row)
    if not order_id: raise RuntimeError(f"exact dynamic active order missing orderId client={_client(row)}")
    response=await private_rest_request("POST","/v1/prediction-markets/order/cancel",params={"orderId":int(order_id) if order_id.isdigit() else order_id},timeout=8.0)
    if response.status_code!=200: raise RuntimeError(f"exact dynamic cancel failed client={_client(row)} http={response.status_code}")
    return {"client":_client(row),"order_id":order_id,"http":response.status_code}


async def public_settlement(event_ticker: str, symbol: str) -> dict[str, Any]:
    """Independent public proof that a filled cycle can no longer carry exposure."""
    if not event_ticker or not symbol:
        return {"settled":False,"reason":"MISSING_EVENT_IDENTITY"}
    async with httpx.AsyncClient(timeout=8.0,follow_redirects=True,headers={"User-Agent":"arb-gemini-l5-dynamic-stop/1.0"}) as http:
        response=await http.get(f"{REST}/v1/prediction-markets/events/{event_ticker}")
    if response.status_code!=200:
        return {"settled":False,"reason":f"PUBLIC_EVENT_HTTP_{response.status_code}"}
    body=response.json()
    if not isinstance(body,Mapping): return {"settled":False,"reason":"PUBLIC_EVENT_MALFORMED"}
    contracts=body.get("contracts")
    contracts=contracts if isinstance(contracts,list) else []
    matches=[row for row in contracts if isinstance(row,Mapping) and str(row.get("instrumentSymbol") or "").upper()==symbol.upper()]
    if len(matches)!=1: return {"settled":False,"reason":f"PUBLIC_CONTRACT_MATCH_COUNT_{len(matches)}"}
    row=matches[0]
    state=str(row.get("status") or row.get("marketState") or "").lower()
    resolution=str(row.get("resolutionSide") or row.get("resolution") or "").lower()
    settled=state=="settled" and resolution in {"yes","no"}
    return {"settled":settled,"state":state or None,"resolution":resolution or None,"symbol":symbol,"event":event_ticker,"mutation":False}


async def run() -> dict[str, Any]:
    state_dir=Path(os.getenv(STATE_DIR_ENV,"/state"))
    session_id=current_session_id(state_dir)
    latest=newest_cycle(state_dir,session_id=session_id)
    payload: dict[str,Any]={}
    if latest is None:
        ids:set[str]=set(); cycle_path=None; cycle_terminal=None; event_ticker=""; symbol=""
    else:
        path,payload=latest; ids=cycle_client_ids(payload); cycle_path=str(path); cycle_terminal=bool(payload.get("terminal")); event_ticker=str(payload.get("event") or ""); symbol=str(payload.get("symbol") or "")
        if session_id is not None and not str(payload.get("sid") or "").startswith(f"{session_id}-c"):
            raise RuntimeError("selected cycle does not belong to current outer session")
        if not ids: raise RuntimeError(f"latest current-session dynamic cycle has no exact client IDs path={path}")

    active_before=await _active(); exact_active=[row for row in active_before if _client(row) in ids]; unknown_active=[row for row in active_before if _client(row) not in ids]
    if unknown_active:
        raise RuntimeError("unknown active prediction orders prevent dynamic upgrade: "+json.dumps([{"client":_client(r),"order_id":_order_id(r)} for r in unknown_active],sort_keys=True))

    cancels=[]
    for row in exact_active: cancels.append(await _cancel_exact(row))
    if exact_active: await asyncio.sleep(0.45)

    active_after,filled_rows,cancelled_rows,positions=await asyncio.gather(_active(),_terminal("filled"),_terminal("cancelled"),_positions())
    remaining_exact=[row for row in active_after if _client(row) in ids]; remaining_unknown=[row for row in active_after if _client(row) not in ids]
    if remaining_exact: raise RuntimeError(f"exact dynamic orders still active after stop count={len(remaining_exact)}")
    if remaining_unknown: raise RuntimeError(f"unknown active prediction orders appeared during stop count={len(remaining_unknown)}")

    best:dict[str,dict[str,Any]]={}
    for row in filled_rows+cancelled_rows:
        client=_client(row)
        if client not in ids: continue
        prior=best.get(client)
        if prior is None or _filled(row)>=_filled(prior): best[client]=row
    fills={client:str(_filled(best.get(client))) for client in sorted(ids)}; any_fill=any(D(value)>ZERO for value in fills.values())

    nonzero_positions=[]
    for row in positions:
        qty=D(str(row.get("totalQuantity") or "0"))
        if qty>ZERO: nonzero_positions.append({"symbol":str(row.get("symbol") or ""),"outcome":str(row.get("eventOutcome") or row.get("outcome") or ""),"qty":str(qty)})
    if nonzero_positions: raise RuntimeError("prediction position remains after dynamic stop; refuse upgrade")

    settlement={"settled":False,"reason":"NO_FILL_REQUIRES_NO_SETTLEMENT"}
    if any_fill:
        settlement=await public_settlement(event_ticker,symbol)
        if not settlement.get("settled"):
            raise RuntimeError(f"exact dynamic cycle filled but is not publicly settled; refuse upgrade settlement={settlement}")

    return {
        "type":"GEMINI_L5_DYNAMIC_EXACT_STOP",
        "current_session_id":session_id,
        "cycle_path":cycle_path,
        "cycle_terminal_before_stop":cycle_terminal,
        "exact_client_ids":sorted(ids),
        "exact_active_before":len(exact_active),
        "exact_cancels":cancels,
        "fills":fills,
        "nonzero_positions":nonzero_positions,
        "settlement":settlement,
        "active_orders_after":0,
        "upgrade_safe":True,
        "order_entry":False,
    }


def main() -> None:
    print(json.dumps(asyncio.run(run()),sort_keys=True,default=str),flush=True)
if __name__=="__main__": main()

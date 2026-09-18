"""The minimum Gemini mutation surface: place, cancel, and the reads to
reconcile them. Nothing else.

It reuses `src.gemini_prediction_auth.private_rest_request`, the existing
reviewed signer, rather than reimplementing HMAC. Every call here is arm-gated
at the point of mutation, so importing this module grants nothing.

CANCELLATION FAILS CLOSED. `cancel` reports CONFIRMED only when the venue says
so or an authoritative read shows the order gone. An unreadable cancel is
reported OUTSTANDING, never assumed successful -- the same rule the PMUS
adapter learned when a status merely lacking "FILLED" was being read as a
cancellation.

There is no cancel_all here. The canary places one order and cancels that
exact order; a blanket cancel is a bigger capability than this needs.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src.gemini_execution import arming, spec

#: a response we could not classify. Never a success and never a retry.
AMBIGUOUS = "AMBIGUOUS"


class VenueError(Exception):
    pass


async def _post(path: str, params: Optional[Dict[str, Any]] = None,
                timeout: float = 15.0) -> Dict[str, Any]:
    """One authenticated POST. Returns a classified result, never raises for a
    venue-level rejection -- a rejection is data."""
    from src.gemini_prediction_auth import private_rest_request
    try:
        r = await private_rest_request("POST", path, timeout=timeout,
                                       params=params)
    except Exception as exc:                                   # noqa: BLE001
        # we do not know whether the venue saw it
        return {"ok": False, "classification": AMBIGUOUS, "status": None,
                "error": type(exc).__name__, "detail": str(exc)[:200]}
    out: Dict[str, Any] = {"status": r.status_code}
    try:
        out["body"] = r.json()
    except ValueError:
        out["body"] = None
        out["unparseable"] = r.text[:300]
    if r.status_code in (200, 201):
        out["ok"] = True
        out["classification"] = "ACCEPTED"
    elif 400 <= r.status_code < 500:
        out["ok"] = False
        out["classification"] = "REJECTED"
    else:
        # 5xx: the venue may or may not have acted
        out["ok"] = False
        out["classification"] = AMBIGUOUS
    return out


async def place(*, symbol: str, side: str, outcome: str, price: str,
                quantity: str, client_order_id: Optional[str] = None,
                now: Optional[float] = None) -> Dict[str, Any]:
    """POST /v1/prediction-markets/order. ARM-GATED.

    `makerOrCancel` is sent as the BOOLEAN the prediction-markets API
    documents, not as a timeInForce value and not as a spot-style options
    array. If the venue cannot honour post-only it cancels the order rather
    than crossing, which is the venue-level guarantee the envelope requires.
    """
    arming.require(now)                      # structural: no arm, no request
    body: Dict[str, Any] = {
        "symbol": symbol,
        "orderType": spec.ORDER_TYPE_LIMIT,
        "side": side,
        "outcome": outcome,
        "price": str(price),                 # strings, per the API
        "quantity": str(quantity),
        "makerOrCancel": True,               # post-only, enforced by the venue
    }
    if client_order_id:
        body["clientOrderId"] = client_order_id
    res = await _post(spec.PLACE_PATH, body)
    res["request"] = body
    return res


async def cancel(order_id: Any, *, now: Optional[float] = None
                 ) -> Dict[str, Any]:
    """POST /v1/prediction-markets/order/cancel for ONE order. ARM-GATED."""
    arming.require(now)
    res = await _post(spec.CANCEL_PATH, {"orderId": order_id})
    res["order_id"] = order_id
    return res


async def active_orders(symbol: Optional[str] = None) -> Dict[str, Any]:
    """POST /v1/prediction-markets/orders/active. A READ; no arm required,
    because reconciliation must work while disarmed."""
    params: Dict[str, Any] = {}
    if symbol:
        params["symbol"] = symbol
    return await _post(spec.ACTIVE_PATH, params or None)


def _orders_of(res: Mapping[str, Any]) -> list:
    b = res.get("body")
    if isinstance(b, list):
        return [o for o in b if isinstance(o, dict)]
    if isinstance(b, dict):
        for k in ("orders", "results", "data"):
            v = b.get(k)
            if isinstance(v, list):
                return [o for o in v if isinstance(o, dict)]
    return []


async def find_order(*, symbol: str, client_order_id: Optional[str] = None,
                     order_id: Any = None) -> Dict[str, Any]:
    """Did our order land? An authoritative READ, used to resolve an ambiguous
    send instead of sending again."""
    res = await active_orders(symbol)
    if not res.get("ok"):
        return {"resolved": False, "why": "active-orders read failed",
                "read": {k: res.get(k) for k in ("status", "classification")}}
    for o in _orders_of(res):
        if order_id is not None and str(o.get(spec.ORDER_ID_FIELD)) == str(order_id):
            return {"resolved": True, "found": True, "order": o}
        if client_order_id and str(o.get("clientOrderId")) == str(client_order_id):
            return {"resolved": True, "found": True, "order": o}
    return {"resolved": True, "found": False,
            "why": ("no matching ACTIVE order. It may never have been "
                    "accepted, or it may have already filled or cancelled -- "
                    "the trade read decides which, and neither is a reason to "
                    "submit again")}


async def cancel_and_confirm(order_id: Any, symbol: str, *,
                             now: Optional[float] = None) -> Dict[str, Any]:
    """Cancel ONE order and establish the outcome, or report OUTSTANDING.

    An unreadable cancel is never reported as cancelled.
    """
    res = await cancel(order_id, now=now)
    if res.get("ok"):
        chk = await find_order(symbol=symbol, order_id=order_id)
        if chk.get("resolved") and not chk.get("found"):
            return {"state": "CANCEL_CONFIRMED", "cancel": res, "check": chk}
        return {"state": "OUTSTANDING", "cancel": res, "check": chk,
                "why": ("the venue accepted the cancel but the order is still "
                        "readable as active; treat as outstanding until it "
                        "is not")}
    return {"state": "OUTSTANDING", "cancel": res,
            "why": ("cancellation status could not be established; an "
                    "unreadable cancel is not a cancellation")}

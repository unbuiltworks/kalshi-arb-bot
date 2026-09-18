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
                timeout: float = 15.0, *, json_body: bool = True
                ) -> Dict[str, Any]:
    """One authenticated POST. Returns a classified result, never raises for a
    venue-level rejection -- a rejection is data.

    `json_body` sends the fields as a real JSON body, which is how every
    Prediction Markets endpoint that takes fields documents them. It defaults
    to True because the alternative silently drops the fields: the classic
    empty-body form is indistinguishable from a correct request right up to
    the venue, which answers 400 without saying what was missing.
    """
    from src.gemini_prediction_auth import (private_rest_json,
                                            private_rest_request)
    send = private_rest_json if json_body else private_rest_request
    try:
        r = await send("POST", path, timeout=timeout, params=params)
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

    THE FIELDS GO IN A JSON BODY. Canary #1 sent this exact dict through the
    classic signer, which transmits Content-Length: 0 and puts the JSON in
    X-GEMINI-PAYLOAD. The venue documents a JSON request body here, so it
    received a request with no fields at all and answered 400 -- not because
    the size or the price was wrong (the contract's own quantityMinimum
    0.01 and priceMinimum 0.01 both admitted them) but because they never
    arrived. See `probe_field_transport` for the read-only proof.
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


async def probe_field_transport(symbol: str) -> Dict[str, Any]:
    """Which request shape does a fields-carrying prediction-markets POST need?

    READ-ONLY, and deliberately so: it asks the question on the active-orders
    endpoint, which takes the same kind of JSON body as the write path and
    changes nothing. It is the only way to establish the transport without
    spending an authorization on it.

    Both forms are sent with identical signed auth headers, so the only
    variable is where the fields live. Reading `symbol` back from an empty
    account proves nothing about filtering -- what is being compared is
    whether the venue ACCEPTS each shape.
    """
    body = {"symbol": symbol, "limit": 50, "offset": 0}
    as_json = await _post(spec.ACTIVE_PATH, dict(body), json_body=True)
    as_header = await _post(spec.ACTIVE_PATH, dict(body), json_body=False)
    verdict = "UNRESOLVED"
    if as_json.get("ok") and not as_header.get("ok"):
        verdict = "JSON_BODY_REQUIRED"
    elif as_json.get("ok") and as_header.get("ok"):
        verdict = "BOTH_ACCEPTED"
    elif as_header.get("ok") and not as_json.get("ok"):
        verdict = "HEADER_PAYLOAD_REQUIRED"
    elif as_json.get("status") is None and as_header.get("status") is None:
        verdict = "UNREADABLE"
    else:
        verdict = "NEITHER_ACCEPTED"
    return {
        "verdict": verdict,
        "path": spec.ACTIVE_PATH,
        "json_body": {k: as_json.get(k) for k in
                      ("status", "classification", "error")},
        "header_payload": {k: as_header.get(k) for k in
                           ("status", "classification", "error")},
        "note": ("a READ endpoint that takes the same kind of JSON body as "
                 "the write path. BOTH_ACCEPTED means the venue tolerates "
                 "either and the transport is not what rejected canary #1; "
                 "JSON_BODY_REQUIRED means it is."),
    }

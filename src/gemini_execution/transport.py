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
        # Not sent by default, and refused outright unless a caller opts in
        # with a documented reason: the OrderRequest schema does not admit it.
        raise VenueError(
            "clientOrderId is not a documented OrderRequest field -- it "
            "appears in the RESPONSE schema only. Canary #1 sent one and was "
            "answered 400. Reconcile by symbol under the one-order invariant "
            "instead of re-adding it.")
    unknown = [k for k in body if k not in spec.ORDER_REQUEST_FIELDS]
    if unknown:
        raise VenueError("undocumented request field(s) %s; a schema that "
                         "rejects unknown properties answers 400" % unknown)
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
    because reconciliation must work while disarmed.

    It asks in the documented JSON-body shape and, if the venue rejects that,
    asks again in the classic shape. Which one this venue wants is exactly
    what is not yet established, and a reconciliation read is the wrong place
    to find out the hard way: an unreadable read here is what makes an
    ambiguous submission unresolvable.
    """
    params: Dict[str, Any] = {}
    if symbol:
        params["symbol"] = symbol
    res = await _post(spec.ACTIVE_PATH, params or None, json_body=True)
    if res.get("ok"):
        res["request_shape"] = "json_body"
        return res
    alt = await _post(spec.ACTIVE_PATH, params or None, json_body=False)
    alt["request_shape"] = "header_payload"
    alt["json_body_attempt"] = {k: res.get(k) for k in
                                ("status", "classification", "error")}
    return alt


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
    orders = _orders_of(res)
    for o in orders:
        if order_id is not None and str(o.get(spec.ORDER_ID_FIELD)) == str(order_id):
            return {"resolved": True, "found": True, "order": o}
        if client_order_id and str(o.get("clientOrderId")) == str(client_order_id):
            return {"resolved": True, "found": True, "order": o}
    if order_id is None and not client_order_id:
        # SYMBOL-SCOPED, and sound only because of the gate that ran first:
        # the canary refuses to submit unless this symbol had ZERO active
        # orders, so a single one found here is necessarily ours. Two are not
        # attributable and this refuses rather than guess.
        if len(orders) == 1:
            return {"resolved": True, "found": True, "order": orders[0],
                    "matched_by": "symbol under the zero-orders-before gate"}
        if len(orders) > 1:
            return {"resolved": False, "found": None,
                    "why": ("%d active orders on this symbol; without a "
                            "client id none of them is attributable"
                            % len(orders))}
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


VOLUME_PATH = "/v1/prediction-markets/metrics/volume"


async def probe_field_transport(ticker_a: str, ticker_b: str) -> Dict[str, Any]:
    """Does the venue read a request field out of X-GEMINI-PAYLOAD, or only
    out of a JSON body? READ-ONLY, and a genuine discriminator.

    WHY THE OBVIOUS PROBE IS NOT ONE. Asking active-orders both ways returns
    200 both ways and settles nothing: its `symbol` is an OPTIONAL filter, so
    an empty body is a perfectly valid request meaning "all orders", and on an
    empty account both answers are the same empty list. A first version of
    this function did exactly that and reported BOTH_ACCEPTED, which reads
    like a finding and is not one.

    So it asks metrics/volume, whose eventTicker actually SELECTS something,
    with two different tickers:

      json(A) == json(B)   the endpoint ignores the field -> INCONCLUSIVE,
                           the probe is wrong, not the venue
      header(A) == json(A) the field WAS read from the signed header
      otherwise            only the body is read, which is what would have
                           silently emptied canary #1's order
    """
    async def ask(tk, json_body):
        r = await _post(VOLUME_PATH, {"eventTicker": tk}, json_body=json_body)
        return r

    ja, jb, ha = (await ask(ticker_a, True), await ask(ticker_b, True),
                  await ask(ticker_a, False))
    brief = lambda r: {k: r.get(k) for k in                      # noqa: E731
                       ("status", "classification", "error")}
    out = {"path": VOLUME_PATH, "ticker_a": ticker_a, "ticker_b": ticker_b,
           "json_a": brief(ja), "json_b": brief(jb), "header_a": brief(ha)}

    if not (ja.get("ok") and jb.get("ok")):
        out["verdict"] = ("NEITHER_ACCEPTED" if ha.get("status") is not None
                          and not ha.get("ok") else "UNREADABLE")
        out["why"] = ("the JSON-body reads did not succeed, so nothing can be "
                      "concluded about the header form")
        return out
    if ja.get("body") == jb.get("body"):
        out["verdict"] = "INCONCLUSIVE"
        out["why"] = ("two different tickers returned the same payload, so "
                      "this endpoint does not discriminate on the field and "
                      "cannot answer the question")
        return out
    out["field_is_honoured"] = True
    if ha.get("ok") and ha.get("body") == ja.get("body"):
        out["verdict"] = "HEADER_PAYLOAD_ALSO_READ"
        out["why"] = ("the venue read eventTicker out of the signed header, "
                      "so an empty body does NOT by itself explain the 400")
    elif ha.get("ok") and ha.get("body") == jb.get("body"):
        out["verdict"] = "JSON_BODY_REQUIRED"
        out["why"] = "the header form returned the OTHER ticker's payload"
    elif ha.get("ok"):
        out["verdict"] = "JSON_BODY_REQUIRED"
        out["why"] = ("the header form succeeded but returned neither "
                      "ticker's payload, so the field was not applied")
    else:
        out["verdict"] = "JSON_BODY_REQUIRED"
        out["why"] = ("the header form was rejected outright while the same "
                      "field in a JSON body was accepted")
    return out

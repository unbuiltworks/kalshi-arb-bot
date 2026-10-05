"""The exact bytes that get signed, built in one place and checked field by
field before anything can sign them.

WHY THIS IS ITS OWN MODULE. Canary #1 was rejected with HTTP 400 and three
hypotheses for it have since been disproven by measurement -- the size, the
price and the transport shape were all fine. What is left is the one thing
in that request the documented OrderRequest schema does not admit: a
clientOrderId, which lives in the RESPONSE schema only. A single undocumented
property is enough to fail a strict validator, and nothing in the code was
positioned to notice one. Now something is, and it sits between construction
and signing rather than alongside it.

WHAT MAY BE IN THE PAYLOAD, and nothing else:

    order   symbol orderType side quantity price outcome timeInForce
            makerOrCancel
    auth    request nonce

`stopPrice` is documented but belongs to stop-limit orders, so it is a schema
field this canary still may not send; the check is EQUALITY against the
allowed set, not containment within the documented one. An extra field fails
closed, and so does a missing one.

TYPES ARE ASSERTED, NOT ASSUMED. makerOrCancel must be a real bool: Python's
True is an int, so `isinstance(x, int)` accepts it and `"true"` is truthy, and
either mistake would send post-only protection the venue may not honour.
Quantity and price are decimal STRINGS validated against the contract's own
quantityMinimum/quantityIncrement and priceMinimum/priceIncrement.
"""
from __future__ import annotations

import json
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Mapping, Optional

from src.gemini_execution import spec

#: EXACTLY the order fields this canary may send. Equality, not subset.
ALLOWED_ORDER_FIELDS = ("symbol", "orderType", "side", "quantity", "price",
                        "outcome", "timeInForce", "makerOrCancel")

#: THE ONE AUTHORIZATION THAT MAY SEND A CROSSING ORDER. `makerOrCancel`
#: False is refused for every other authorization, so an ordinary strategy
#: cannot obtain taker behaviour merely by passing a flag: it would also have
#: to be running under this exact id, and an authorization is one-shot and is
#: spent once consumed. The execution-semantics probe exists to test whether
#: "no executable offer" really means "cannot buy", which a post-only order
#: cannot test -- the venue cancels it rather than crossing. Nothing else in
#: this repository needs taker behaviour, and nothing else may have it.
#: MOVED 2026-10-03 to the R2 execution-validation cohort. This constant and
#: arming.EXPECTED_AUTH_ID must AGREE for a crossing order to be constructible
#: at all; the launcher asserts it before anything else runs. SEMANTICS-1 is
#: spent, so leaving this on the old id would have shut the taker path.
#: MOVED 2026-10-03 to CI15M-2, whose Treatment T is a CAPPED MARKETABLE
#: LIMIT and therefore needs `makerOrCancel: false`. This constant and
#: `arming.EXPECTED_AUTH_ID` must AGREE for a crossing order to be
#: constructible, and CI15M-2 is the only authorization that grants it.
#: It is still a LIMIT order with an explicit price: `orderType` stays
#: "limit" and the price is bounded by the policy's own 0.90 ceiling, so
#: this is not and cannot become an unrestricted market order.
#: MOVED 2026-10-05 to the BTC5m EXECUTION CANARY. The canary's entry is a
#: CAPPED MARKETABLE LIMIT and therefore needs `makerOrCancel: false`. This
#: constant and `arming.EXPECTED_AUTH_ID` must AGREE for a crossing order to
#: be constructible at all, and exactly one authorization holds that key at a
#: time -- so this move simultaneously REVOKES crossing orders for CI15M-4.
#: It remains a LIMIT order with an explicit price bounded by the canary's own
#: 0.90 acquisition cap; it is not and cannot become a market order.
SEMANTICS_PROBE_AUTH_ID = "OWNER-2026-10-05-GEMINI-BTC5M-CANARY-1"

#: fields that must never appear, with the reason, so a refusal explains itself
FORBIDDEN = {
    "clientOrderId": ("not in the documented OrderRequest schema -- it is a "
                      "RESPONSE field. Canary #1 sent one and was answered "
                      "400."),
    "stopPrice": "belongs to stop-limit orders; this canary is a limit order",
    "options": "the SPOT schema's maker-or-cancel array, not this API's",
    "amount": "the SPOT schema's size field; this API takes `quantity`",
    "type": "the account WebSocket's field name, not the REST one",
    "eventOutcome": "the account WebSocket's field name; REST takes `outcome`",
}


class PayloadViolation(Exception):
    """Raised before anything is signed. Never downgraded to a warning."""


def _is_decimal_string(v: Any) -> bool:
    if not isinstance(v, str) or not v.strip():
        return False
    try:
        D(v)
    except (InvalidOperation, ValueError):
        return False
    return True


def build_order(*, symbol: str, price: str, quantity: str,
                side: str = spec.SIDE_BUY, outcome: str = spec.OUTCOME_YES,
                maker_or_cancel: bool = True
                ) -> Dict[str, Any]:
    """The order body, in a fixed field order so the rendering is stable.

    `maker_or_cancel` DEFAULTS TO TRUE and every pre-existing caller omits it,
    so ordinary execution is post-only exactly as before. False is reachable
    only through `transport.place(allow_taker=True)`, which additionally
    requires the live authorization to be SEMANTICS_PROBE_AUTH_ID.
    """
    if type(maker_or_cancel) is not bool:
        raise PayloadViolation(
            "maker_or_cancel must be a real bool, got %r" % (maker_or_cancel,))
    return {
        "symbol": symbol,
        "orderType": spec.ORDER_TYPE_LIMIT,
        "side": side,
        "quantity": str(quantity),
        "price": str(price),
        "outcome": outcome,
        "timeInForce": spec.TIME_IN_FORCE_GTC,
        "makerOrCancel": maker_or_cancel,
    }


def assert_types(order: Mapping[str, Any], *,
                 allow_taker: bool = False) -> Dict[str, str]:
    """Every field's type and admissible value, stated rather than trusted.

    `allow_taker` DEFAULTS TO FALSE, so makerOrCancel must still be exactly
    boolean True for every caller that does not opt in -- the pre-existing
    guarantee, unchanged. Opting in widens that ONE value and nothing else:
    the order is still a LIMIT order and every other field is checked
    identically.
    """
    checks: Dict[str, Any] = {
        "symbol": (isinstance(order.get("symbol"), str)
                   and bool(order.get("symbol"))),
        "orderType": order.get("orderType") == spec.ORDER_TYPE_LIMIT,
        "side": order.get("side") in (spec.SIDE_BUY, spec.SIDE_SELL),
        "quantity": _is_decimal_string(order.get("quantity")),
        "price": _is_decimal_string(order.get("price")),
        "outcome": order.get("outcome") in (spec.OUTCOME_YES, spec.OUTCOME_NO),
        "timeInForce": order.get("timeInForce") == spec.TIME_IN_FORCE_GTC,
        # `type(...) is bool`, NOT isinstance: True is an int and "true" is
        # truthy, and either would quietly send something else.
        # `type(...) is bool` either way: True is an int and "true" is truthy.
        # allow_taker widens the admissible VALUE, never the type.
        "makerOrCancel": (type(order.get("makerOrCancel")) is bool
                          and (order.get("makerOrCancel") is True
                               or (allow_taker
                                   and order.get("makerOrCancel") is False))),
    }
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        raise PayloadViolation(
            "field(s) %s fail their type/value assertion: %s"
            % (bad, {k: repr(order.get(k)) for k in bad}))
    return {
        "symbol": "string",
        "orderType": 'string, exactly "limit"',
        "side": 'string, exactly "%s"' % order["side"],
        "quantity": "decimal string, from contract metadata",
        "price": "decimal string, aligned to contract metadata",
        "outcome": 'string, exactly "%s"' % order["outcome"],
        "timeInForce": 'string, exactly "good-til-cancel"',
        "makerOrCancel": "boolean true (not 1, not \"true\")",
    }


def validate_structure(order: Mapping[str, Any]) -> Dict[str, Any]:
    """Structural comparison against the documented schema. FAILS CLOSED."""
    keys = set(order)
    allowed = set(ALLOWED_ORDER_FIELDS)
    unknown = sorted(keys - allowed)
    missing = sorted(allowed - keys)
    named = {k: FORBIDDEN[k] for k in unknown if k in FORBIDDEN}
    if unknown:
        raise PayloadViolation(
            "unknown order field(s) %s; a strict validator answers 400 on "
            "any one of them%s"
            % (unknown, (" -- " + json.dumps(named)) if named else ""))
    if missing:
        raise PayloadViolation("missing required order field(s) %s" % missing)
    off_schema = sorted(allowed - set(spec.ORDER_REQUEST_FIELDS))
    if off_schema:
        raise PayloadViolation(
            "the allowed set has drifted off the documented schema: %s"
            % off_schema)
    return {"unknown": [], "missing": [],
            "allowed": list(ALLOWED_ORDER_FIELDS),
            "documented_schema": list(spec.ORDER_REQUEST_FIELDS),
            "not_sent_though_documented": sorted(
                set(spec.ORDER_REQUEST_FIELDS) - allowed)}


def signing_payload(order: Mapping[str, Any], *, path: str = spec.PLACE_PATH,
                    nonce: Optional[int] = None) -> Dict[str, Any]:
    """Exactly what gets base64-encoded and HMAC-signed, in signing order.

    Validated first: nothing reaches this function's output without passing
    both the structural check and the type assertions.
    """
    validate_structure(order)
    assert_types(order)
    if nonce is not None and type(nonce) is not int:
        raise PayloadViolation("nonce must be an int, got %r" % type(nonce))
    if nonce is None:
        # A REAL epoch-seconds value, not 0. The point of rendering this is to
        # show what would actually be signed, and a placeholder that could
        # never be sent is not that. Read from the clock rather than from
        # next_nonce(), because rendering a payload must not advance the
        # signer's nonce counter as a side effect.
        import time as _t
        nonce = int(_t.time())
    out: Dict[str, Any] = {"request": path, "nonce": nonce}
    out.update(order)
    extra = sorted(set(out) - set(spec.AUTH_PAYLOAD_FIELDS)
                   - set(ALLOWED_ORDER_FIELDS))
    if extra:                                        # pragma: no cover
        raise PayloadViolation("payload carries %s beyond order+auth" % extra)
    return out


def sanitized_decoded(order: Mapping[str, Any], *,
                      path: str = spec.PLACE_PATH,
                      nonce: Optional[int] = None) -> Dict[str, Any]:
    """The decoded X-GEMINI-PAYLOAD as it would be immediately before signing.

    SANITIZED means no API key, no secret and no signature -- none of which
    live in the payload in the first place; they travel in headers of their
    own. The payload itself carries nothing secret, so it is shown whole.
    """
    body = signing_payload(order, path=path, nonce=nonce)
    return {
        "decoded_payload": body,
        "decoded_payload_json": json.dumps(body, separators=(",", ":")),
        "order_fields": list(ALLOWED_ORDER_FIELDS),
        "auth_fields": list(spec.AUTH_PAYLOAD_FIELDS),
        "types": assert_types(order),
        "structure": validate_structure(order),
        "nonce_note": ("epoch-seconds int, regenerated per request; it is the "
                       "ONLY part of this payload that varies between "
                       "requests"),
        "sanitization": ("nothing is redacted because nothing secret is "
                         "present: the API key, the signature and the secret "
                         "are never payload fields"),
        "request_body_note": ("the HTTP body carries the ORDER fields only. "
                              "request/nonce are payload-only, and this venue "
                              "reads the fields from either place (measured "
                              "2026-09-18)"),
    }


# =====================================================================
# THE WEBSOCKET DIALECT -- the transport this account has actually used.
#
# Same order model, different spelling: upper-case side and outcome, `type`
# rather than `orderType`, `eventOutcome` rather than `outcome`, and
# maker-only as timeInForce "MOC" rather than a boolean. The venue normalises
# its own records to the REST spelling, so these are two dialects over one
# model -- which is exactly why sending one dialect's field names down the
# other's transport has to be refused by name rather than hoped against.

#: EXACTLY the params of the known-good frame. Equality, not subset.
WS_ALLOWED_FIELDS = spec.WS_ORDER_FIELDS

#: REST spellings, which are wrong HERE, and the spot shapes, which are wrong
#: everywhere. Each refuses by name so a refusal explains itself.
WS_FORBIDDEN = {
    "orderType": 'the REST spelling; this transport takes `type`',
    "outcome": 'the REST spelling; this transport takes `eventOutcome`',
    "makerOrCancel": 'the REST boolean; here maker-only is timeInForce "MOC"',
    "options": "the SPOT maker-or-cancel array, not this API's",
    "amount": "the SPOT size field; this API takes `quantity`",
    "stopPrice": "belongs to stop orders; this canary is a limit order",
}


def build_ws_order(*, symbol: str, price: str, quantity: str,
                   client_order_id: str, outcome: str = spec.WS_OUTCOME_YES,
                   side: str = spec.WS_SIDE_BUY) -> Dict[str, Any]:
    """The params of the known-good frame, in its field order.

    clientOrderId IS sent here, unlike on REST. It is not a guess: it is in
    the recovered frame, and the venue stores and returns it on this
    account's own orders -- which is what makes reconciliation authoritative
    rather than positional.
    """
    return {
        "symbol": symbol,
        "side": side,
        "type": spec.WS_TYPE_LIMIT,
        "timeInForce": spec.WS_TIF_MAKER_ONLY,
        "price": str(price),
        "quantity": str(quantity),
        "eventOutcome": outcome,
        "clientOrderId": str(client_order_id),
    }


def assert_ws_types(params: Mapping[str, Any]) -> Dict[str, str]:
    cid = params.get("clientOrderId")
    checks = {
        "symbol": (isinstance(params.get("symbol"), str)
                   and bool(params.get("symbol"))),
        "side": params.get("side") in (spec.WS_SIDE_BUY, "SELL"),
        "type": params.get("type") == spec.WS_TYPE_LIMIT,
        # MAKER-ONLY IS THE WHOLE POINT. good-til-cancel, IOC and FOK are all
        # valid values here and none of them is post-only; both recovered
        # historical rows were fill-or-kill, i.e. takers.
        "timeInForce": params.get("timeInForce") == spec.WS_TIF_MAKER_ONLY,
        "price": _is_decimal_string(params.get("price")),
        "quantity": _is_decimal_string(params.get("quantity")),
        "eventOutcome": params.get("eventOutcome") in (spec.WS_OUTCOME_YES,
                                                       spec.WS_OUTCOME_NO),
        "clientOrderId": (isinstance(cid, str) and 0 < len(cid)
                          <= spec.WS_CLIENT_ID_MAX),
    }
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        raise PayloadViolation(
            "WS field(s) %s fail their type/value assertion: %s"
            % (bad, {k: repr(params.get(k)) for k in bad}))
    return {
        "symbol": "string",
        "side": 'string, exactly "BUY"',
        "type": 'string, exactly "LIMIT"',
        "timeInForce": 'string, exactly "MOC" -- maker-only, not GTC/IOC/FOK',
        "price": "decimal string, aligned to contract metadata",
        "quantity": "decimal string, from contract metadata",
        "eventOutcome": 'string, exactly "%s"' % params["eventOutcome"],
        "clientOrderId": "string, 1..%d chars" % spec.WS_CLIENT_ID_MAX,
    }


def validate_ws_structure(params: Mapping[str, Any]) -> Dict[str, Any]:
    keys = set(params)
    allowed = set(WS_ALLOWED_FIELDS)
    unknown = sorted(keys - allowed)
    missing = sorted(allowed - keys)
    named = {k: WS_FORBIDDEN[k] for k in unknown if k in WS_FORBIDDEN}
    if unknown:
        raise PayloadViolation(
            "unknown WS param(s) %s%s" % (unknown, (" -- " + json.dumps(named))
                                          if named else ""))
    if missing:
        raise PayloadViolation("missing WS param(s) %s" % missing)
    return {"unknown": [], "missing": [], "allowed": list(WS_ALLOWED_FIELDS)}


def sanitized_ws_frame(params: Mapping[str, Any], *,
                       request_id: str = "<uuid4 hex, per request>",
                       method: str = spec.WS_PLACE_METHOD) -> Dict[str, Any]:
    """The exact frame, as it would go on the socket, with nothing removed.

    SANITIZED is close to a no-op here and that is the point: unlike the REST
    path there is no per-message nonce, payload or signature to redact. The
    socket was authenticated once, at the handshake, so the frame carries no
    credential material of any kind.
    """
    validate_ws_structure(params)
    types = assert_ws_types(params)
    frame = {"id": request_id, "method": method, "params": dict(params)}
    return {
        "frame": frame,
        "frame_json": json.dumps(frame, separators=(",", ":")),
        "types": types,
        "structure": validate_ws_structure(params),
        "auth_note": ("no per-message nonce, payload or signature: the socket "
                      "is authenticated once at the handshake by "
                      "websocket_auth_headers, and the frame carries no "
                      "credential material"),
        "ack_contract": "accepted iff the ack's `status` is %d"
                        % spec.WS_ACK_OK,
    }

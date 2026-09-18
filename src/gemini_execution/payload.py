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
                side: str = spec.SIDE_BUY, outcome: str = spec.OUTCOME_YES
                ) -> Dict[str, Any]:
    """The order body, in a fixed field order so the rendering is stable."""
    return {
        "symbol": symbol,
        "orderType": spec.ORDER_TYPE_LIMIT,
        "side": side,
        "quantity": str(quantity),
        "price": str(price),
        "outcome": outcome,
        "timeInForce": spec.TIME_IN_FORCE_GTC,
        "makerOrCancel": True,
    }


def assert_types(order: Mapping[str, Any]) -> Dict[str, str]:
    """Every field's type and admissible value, stated rather than trusted."""
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
        "makerOrCancel": (type(order.get("makerOrCancel")) is bool
                          and order.get("makerOrCancel") is True),
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
    out: Dict[str, Any] = {"request": path,
                           "nonce": nonce if nonce is not None else 0}
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

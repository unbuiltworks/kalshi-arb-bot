"""The single place an order body is serialized.

Preview and submit must send the same bytes, or a green preview proves
nothing about the order that follows it. So neither builds a body of its
own: both call `build()`, and a test asserts submit has no other path to a
request body.

Two representations exist ON PURPOSE, and only while the question is open.

`LEGACY` is what `PmusTransport.submit()` has always sent:

    {"clientOrderId":…, "marketSlug":…, "outcome":"UP", "side":"BUY",
     "price":"0.47", "quantity":"1.00", "type":"LIMIT"}

`DOCUMENTED` is what the venue's published OrdersService schema
(https://docs.polymarket.us/api-reference/oapi-schemas/orders-schema.json,
CreateOrderRequest) says the field names and types are:

    {"marketSlug":…, "type":"ORDER_TYPE_LIMIT",
     "price":{"value":"0.47","currency":"USD"}, "quantity":1.0,
     "outcomeSide":"OUTCOME_SIDE_YES", "action":"ORDER_ACTION_BUY",
     "tif":…, "participateDontInitiate":…}

They disagree on every field that matters: the outcome axis (`outcome`
UP/DOWN vs `outcomeSide` YES/NO), the buy/sell field (`side` vs `action`),
the enum spellings, and whether price is a scalar or an Amount. The schema
also does not carry `clientOrderId` at all.

Which one the venue actually accepts is an EMPIRICAL question, and it is
answered by previewing both rather than by reading the documentation and
assuming. Until that evidence exists, `DEFAULT_SCHEMA` stays LEGACY so this
module changes no execution behaviour.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping

LEGACY = "LEGACY"
DOCUMENTED = "DOCUMENTED"

#: what submit() sends today. Changed only once a preview has shown which
#: representation the venue accepts.
DEFAULT_SCHEMA = LEGACY

#: fields that exist only because a request is a preview, and so are
#: expected to differ between a preview body and the submit body.
PREVIEW_ONLY_FIELDS = ("clientOrderId",)


def build(intent: Mapping[str, Any], market_slug: str,
          schema: str = None) -> Dict[str, Any]:
    """The request body for one order, in the named representation.

    `intent` is the executor's proposal and is never reinterpreted here:
    side, price, quantity and maker/taker are carried through exactly as
    the frozen engine produced them. This function chooses field NAMES and
    TYPES, nothing else.
    """
    schema = schema or DEFAULT_SCHEMA
    side = str(intent["side"]).upper()
    maker = str(intent.get("mode", "")).upper() == "MAKER"

    if schema == LEGACY:
        return {
            "clientOrderId": intent["client_order_id"],
            "marketSlug": market_slug,
            "outcome": side,
            "side": "BUY",
            "price": str(intent["price"]),
            "quantity": str(intent["qty"]),
            "type": "LIMIT" if maker else "MARKET",
        }

    if schema == DOCUMENTED:
        from src.l5_preview_session import outcome_side_for
        return {
            "marketSlug": market_slug,
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(intent["price"]), "currency": "USD"},
            "quantity": float(D(str(intent["qty"]))),
            "outcomeSide": outcome_side_for(side),
            "action": "ORDER_ACTION_BUY",
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": maker,
        }

    raise ValueError("unknown order schema %r" % (schema,))


def preview_envelope(body: Mapping[str, Any]) -> Dict[str, Any]:
    """The preview endpoint wraps the same body under `request`."""
    return {"request": dict(body)}


def differences(preview_body: Mapping[str, Any],
                submit_body: Mapping[str, Any]) -> Dict[str, Any]:
    """Every field where a previewed body and a submitted body disagree.

    Used to assert that the only differences are intrinsically
    preview-specific, so a green preview cannot bless a different order.
    """
    keys = set(preview_body) | set(submit_body)
    diff = {k: {"preview": preview_body.get(k), "submit": submit_body.get(k)}
            for k in sorted(keys)
            if preview_body.get(k) != submit_body.get(k)}
    unexpected = sorted(k for k in diff if k not in PREVIEW_ONLY_FIELDS)
    return {"differences": diff, "unexpected": unexpected,
            "identical_but_for_preview_fields": not unexpected}

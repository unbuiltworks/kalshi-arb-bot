"""THE order serializer. One implementation, shared by everything.

This is the body that has actually traded on Polymarket US. It was proven in
`pmus_micro_executor._body`, which now delegates here rather than keeping a
copy, so there is exactly one implementation and not a family of them.

Why it lives in its own module
------------------------------
`l5_pmus_transport` is COPY'd into the 24/7 observer image; `pmus_q1_executor`
and `pmus_micro_executor` deliberately are NOT, because they can place, amend
and cancel orders. So the transport could not import the proven builder, and
wrote its own body instead -- which is how it came to send `outcome`/`side`/
`LIMIT` and a bare-string price, none of which the venue accepts.

This module carries the serialization ONLY. It has no credentials, no
transport, no endpoint and no import of any executor, so sharing it adds no
order path to the observer image.

The two hard-won conventions it encodes
---------------------------------------
1. `price.value` is ALWAYS the long (YES) side's price, whatever the outcome
   side. To buy NO at X the wire value is 1.00 - X. Live on 2026-09-05/06,
   24 of 24 NO-side post-only orders priced in NO terms were rejected,
   because the venue read them as NO at 1-X, which crosses. Internally every
   price stays in its own outcome's terms; only the wire value maps.
2. `quantity` is a JSON number, not a string -- the venue documents
   number<double> and it is what fractional quantity depends on.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping

ONE = D("1")

#: fields a preview request carries that a submit body does not, and vice
#: versa. Empty: preview and submit send the SAME body, and the preview
#: endpoint merely wraps it (see `preview_envelope`). Kept so the drift
#: assertion has something to name if that ever stops being true.
PREVIEW_ONLY_FIELDS: tuple = ()

#: UP/DOWN is the engine's vocabulary; YES/NO is the venue's.
#: Provenance for the mapping is in l5_preview_session.STATIC_CONTRACT.
#: Accepts the engine's vocabulary AND the venue's own enum unchanged, so
#: the proven executor -- which already speaks OUTCOME_SIDE_* -- can pass its
#: value straight through without a lossy round trip.
_SIDE = {"UP": "OUTCOME_SIDE_YES", "DOWN": "OUTCOME_SIDE_NO",
         "YES": "OUTCOME_SIDE_YES", "NO": "OUTCOME_SIDE_NO",
         "OUTCOME_SIDE_YES": "OUTCOME_SIDE_YES",
         "OUTCOME_SIDE_NO": "OUTCOME_SIDE_NO"}


def outcome_side(side: str) -> str:
    try:
        return _SIDE[str(side).upper()]
    except KeyError:
        raise ValueError("no venue outcome side for %r" % (side,)) from None


def venue_price(outcome_side_str: str, price: D) -> D:
    """Outcome-terms price -> the venue's long-side price.value."""
    px = D(str(price))
    return ONE - px if str(outcome_side_str).upper().endswith("NO") else px


def build(*, slug: str, side: str, price: Any, qty: Any,
          post_only: bool) -> Dict[str, Any]:
    """The venue-accepted order body. Proven representation, verbatim.

    Callers supply WHAT to trade -- market, outcome, price in that outcome's
    own terms, quantity, maker/taker. This decides HOW to say it.
    """
    q = D(str(qty))
    if q <= 0:
        raise ValueError("non-positive quantity %r" % (qty,))
    os_ = outcome_side(side)
    return {
        "marketSlug": slug,
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(venue_price(os_, price)), "currency": "USD"},
        "quantity": float(q),
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        "participateDontInitiate": bool(post_only),
        "outcomeSide": os_,
        "action": "ORDER_ACTION_BUY",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }


def from_intent(intent: Mapping[str, Any], market_slug: str) -> Dict[str, Any]:
    """The L5 engine's intent -> the proven body. No reinterpretation:
    side, price, quantity and maker/taker are carried through as the frozen
    engine produced them."""
    return build(slug=intent.get("market") or market_slug,
                 side=intent["side"], price=intent["price"],
                 qty=intent["qty"],
                 post_only=str(intent.get("mode", "")).upper() == "MAKER")


def preview_envelope(body: Mapping[str, Any]) -> Dict[str, Any]:
    """The preview endpoint takes the same body under `request`, exactly as
    the proven executor sends it."""
    return {"request": dict(body)}


def differences(preview_body: Mapping[str, Any],
                submit_body: Mapping[str, Any]) -> Dict[str, Any]:
    keys = set(preview_body) | set(submit_body)
    diff = {k: {"preview": preview_body.get(k), "submit": submit_body.get(k)}
            for k in sorted(keys)
            if preview_body.get(k) != submit_body.get(k)}
    unexpected = sorted(k for k in diff if k not in PREVIEW_ONLY_FIELDS)
    return {"differences": diff, "unexpected": unexpected,
            "identical_but_for_preview_fields": not unexpected}

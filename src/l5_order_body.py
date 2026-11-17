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
#: Provenance for the mapping is in STATIC_CONTRACT just above.
#: Accepts the engine's vocabulary AND the venue's own enum unchanged, so
#: the proven executor -- which already speaks OUTCOME_SIDE_* -- can pass its
#: value straight through without a lossy round trip.
#: Up/Down semantics from Polymarket US's published contract definition, not
#: inferred from prices, history or L5's behaviour. It lives HERE, in the
#: module with no transport and no credentials, because the preflight reads
#: it and the preflight runs inside the GET-only observer image; importing it
#: from the preview session would drag a POST-capable transport in with it.
STATIC_CONTRACT: Dict[str, Any] = {
    "series": "btc-updown-15m",
    "crypto_market_type": "updown",
    "asset_price_market_type": "ASSET_PRICE_MARKET_TYPE_UP_DOWN",
    "yes_is": "UP",
    "no_is": "DOWN",
    "basis": (
        "One binary instrument per window. Long side pays when the close is "
        "at or above the open."),
    "provenance": [
        {"source": "Polymarket US Trader Guide -- Crypto Schema",
         "url": "https://docs.polymarket.us/trader-guide/crypto-schema",
         "quote": "updown | ASSET_PRICE_MARKET_TYPE_UP_DOWN | One binary "
                  "instrument per window. Long side pays when the close is "
                  "at or above the open."},
        {"source": "Polymarket US Crypto FAQs",
         "url": "https://docs.polymarket.us/faqs/crypto-faqs",
         "quote": "a flat Up/Down window settles Up"},
        {"source": "Polymarket US -- Market Structure",
         "url": "https://docs.polymarket.us/learn/trading/basics/"
                "buying-yes-vs-selling-no",
         "quote": "Polymarket US markets use a single instrument "
                  "(buying Yes vs selling No)."},
    ],
    "retrieved_utc": "2026-09-24",
    "LABEL": "STATIC_CONTRACT_DEFINITION -- documentation, not a live read",
}

#: Contract attributes the same source publishes for this family. Recorded
#: for comparison against what the venue actually enforces; NEVER used as
#: the value sent in an order.
STATIC_FAMILY_ATTRS: Dict[str, Any] = {
    "tick_size": "0.01",
    "valid_price_range": ["0.01", "0.99"],
    "minimum_order_contracts": "0.01",
    "payout_per_contract_usd": "1.00",
    "LABEL": "DOCUMENTED FAMILY DEFAULTS -- not a per-instrument read",
}


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


#: WHAT THE VENUE DOCUMENTS, read from docs.polymarket.us
#: /api-reference/orders/create-order on 2026-11-17. Recorded here
#: because this programme had been ASSUMING PMUS offered only
#: GOOD_TILL_CANCEL, which is what this builder sent, and assuming no
#: batch route existed. Both assumptions were wrong.
#:
#:   tif   TIME_IN_FORCE_DAY | GOOD_TILL_CANCEL | GOOD_TILL_DATE
#:         | IMMEDIATE_OR_CANCEL | FILL_OR_KILL
#:   type  ORDER_TYPE_LIMIT | ORDER_TYPE_MARKET
#:   participateDontInitiate  "If true, order must rest on the book prior
#:                             to matching (maker only)"
#:   also documented: goodTillTime, synchronousExecution, maxBlockTime,
#:   slippageTolerance {bips, ticks}
#:   batch routes: POST /v1/orders/batched, /batched/cancel, /batched/modify
#:
#: DOCUMENTED IS NOT ACCEPTED. None of these has ever been sent by this
#: adapter, and acceptance for THIS account and market is unverified: the
#: credentials live on the Railway service, not in a research container,
#: so it can only be checked through /v1/order/preview from inside the
#: lane. Until it is, the proven body is what ships.
TIF_GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
TIF_FOK = "TIME_IN_FORCE_FILL_OR_KILL"
TIF_IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
DOCUMENTED_TIF = (TIF_GTC, TIF_FOK, TIF_IOC,
                  "TIME_IN_FORCE_DAY", "TIME_IN_FORCE_GOOD_TILL_DATE")
#: a crossing completion is the ONLY place a non-GTC tif is permitted
COMPLETION_TIFS = (TIF_FOK, TIF_IOC)


def build(*, slug: str, side: str, price: Any, qty: Any,
          post_only: bool, tif: Any = None,
          slippage_ticks: Any = None) -> Dict[str, Any]:
    """The venue-accepted order body. Proven representation, verbatim.

    Callers supply WHAT to trade -- market, outcome, price in that outcome's
    own terms, quantity, maker/taker. This decides HOW to say it.

    `tif` and `slippage_ticks` DEFAULT TO ABSENT and the body is then
    byte-identical to the one the venue has been accepting all along --
    `test_the_default_body_is_byte_identical` asserts it. They exist so a
    crossing COMPLETION can ask for FILL_OR_KILL, which is the venue's own
    answer to partial completion: fill the whole clip or none of it, rather
    than leaving half a hedge against the residual it was retiring.
    """
    q = D(str(qty))
    if q <= 0:
        raise ValueError("non-positive quantity %r" % (qty,))
    os_ = outcome_side(side)
    use_tif = TIF_GTC if tif is None else str(tif)
    if use_tif not in DOCUMENTED_TIF:
        raise ValueError("tif %r is not documented by the venue" % (tif,))
    if use_tif != TIF_GTC and post_only:
        #: "must rest on the book prior to matching" cannot be combined
        #: with "fill immediately or kill"; the venue would be asked for
        #: two contradictory things and the safe reading is to refuse
        raise ValueError("post_only cannot be combined with %s" % use_tif)
    body = {
        "marketSlug": slug,
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(venue_price(os_, price)), "currency": "USD"},
        "quantity": float(q),
        "tif": use_tif,
        "participateDontInitiate": bool(post_only),
        "outcomeSide": os_,
        "action": "ORDER_ACTION_BUY",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }
    if slippage_ticks is not None:
        t = int(slippage_ticks)
        if t < 0:
            raise ValueError("negative slippage_ticks %r" % (slippage_ticks,))
        #: VENUE-ENFORCED, rather than our own estimate of what we might
        #: pay. It can only ever reject a worse fill than asked for.
        body["slippageTolerance"] = {"ticks": t}
    return body


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

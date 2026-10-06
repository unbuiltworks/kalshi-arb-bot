"""The exact Attempt-5 state, frozen offline, so the wire shape is a test
rather than a live experiment.

Every value here was read out of Attempt 5's own EXEC1XR2_RECORD on
deployment dcb9249a, not reconstructed. The point of the fixture is that
the next live probe should be unnecessary to establish the wire format:
if the payload is wrong, this fails in CI in milliseconds for $0.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict

#: Attempt 5, verbatim.
A5: Dict[str, Any] = {
    "slug": "cpc-btc-updown-15m-2026-10-06-2300z",
    "session_id": "xr2-a5-2026-10-06-2066fd02",
    "semantic_side_label": "DOWN",
    "semantic_outcome_side": "OUTCOME_SIDE_NO",
    "semantic_price_own_terms": D("0.49"),
    "quantity": D("0.01"),
    "tick": D("0.01"),
    "min_qty": D("0.01"),
    "venue_bid_yes": D("0.50"),
    "venue_ask_yes": D("0.51"),
    "own_side_touch": D("0.49"),
    "offset_ticks_from_touch": 0,
    "post_only": True,
    "side_digest":
        "61ab8047a60800429ba94178130909f174f538af4065991e1a42ee3565c0f581",
}

#: What Attempt 5 actually put on the wire. KEPT AS EVIDENCE, not as a
#: target: this is the payload the venue answered 400 to.
A5_SENT_PREVIEW_REQUEST: Dict[str, Any] = {
    "marketSlug": "cpc-btc-updown-15m-2026-10-06-2300z",
    "type": "ORDER_TYPE_LIMIT",
    "price": {"value": "0.51"},
    "quantity": "0.01",
    "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
    "participateDontInitiate": True,
    "outcomeSide": "OUTCOME_SIDE_NO",
    "action": "ORDER_ACTION_BUY",
    "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
}

#: The corrected order body: what `src.l5_order_body.build` produces for the
#: same semantic intent. The three corrected fields are `price.currency`,
#: the numeric `quantity`, and -- for the preview call -- the envelope.
A5_CORRECTED_ORDER_BODY: Dict[str, Any] = {
    "marketSlug": "cpc-btc-updown-15m-2026-10-06-2300z",
    "type": "ORDER_TYPE_LIMIT",
    "price": {"value": "0.51", "currency": "USD"},
    "quantity": 0.01,
    "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
    "participateDontInitiate": True,
    "outcomeSide": "OUTCOME_SIDE_NO",
    "action": "ORDER_ACTION_BUY",
    "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
}

#: The corrected PREVIEW request: the same body under `request`.
A5_CORRECTED_PREVIEW_REQUEST: Dict[str, Any] = {
    "request": dict(A5_CORRECTED_ORDER_BODY)}

#: The three deviations, named. Anything else differing is a regression.
A5_DEFECTS = (
    "price.currency",
    "quantity",
    "preview_envelope",
)

#: ------------------------------------------------------------------------
#: THE COMPLEMENT CHAIN, WRITTEN OUT ONCE AND FOR ALL.
#:
#: This is the ambiguity that cost 24 of 24 live NO-side orders on
#: 2026-09-05/06, and it is pinned here so it can never be re-derived from
#: memory or from a price.
#:
#:   semantic side        DOWN
#:     -> venue outcome   outcomeSide = OUTCOME_SIDE_NO     (DOWN is NO)
#:     -> venue action    action      = ORDER_ACTION_BUY    (always BUY;
#:                                      this venue is one instrument, so
#:                                      DOWN is BUY NO, never SELL YES)
#:   semantic price       0.49 in DOWN's OWN terms
#:     -> wire price      price.value = 1.00 - 0.49 = 0.51
#:                        because price.value is ALWAYS the long (YES)
#:                        side's price, whatever outcomeSide says.
#:
#: BOTH fields are sent, and they are NOT redundant: outcomeSide says WHICH
#: outcome is bought, price.value says WHERE on the YES axis. Sending the
#: NO price in NO terms alongside OUTCOME_SIDE_NO is the historical bug --
#: the venue reads it as NO at 1-X, which crosses.
#:
#: The internal direction is the inverse: a wire value W on OUTCOME_SIDE_NO
#: means a semantic DOWN price of 1 - W. Internally every price stays in
#: its own outcome's terms; ONLY the wire value maps.
#: ------------------------------------------------------------------------
COMPLEMENT_CHAIN: Dict[str, Any] = {
    "semantic_side": "DOWN",
    "wire_outcome_side": "OUTCOME_SIDE_NO",
    "wire_action": "ORDER_ACTION_BUY",
    "semantic_price_own_terms": "0.49",
    "wire_price_value": "0.51",
    "wire_price_currency": "USD",
    "rule": "price.value = 1 - semantic_price when outcomeSide ends in NO",
    "inverse": "semantic_price = 1 - price.value on OUTCOME_SIDE_NO",
    "up_is_yes_price_sent_unchanged": True,
    "both_fields_are_required_and_not_redundant": True,
    "historical_failure": ("2026-09-05/06: 24 of 24 NO-side post-only "
                           "orders priced in NO terms were rejected"),
    "a5_got_this_right": True,
}

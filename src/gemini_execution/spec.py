"""The Gemini prediction-market order API, transcribed from the official docs.

VERIFIED 2026-09-17 against developer.gemini.com, NOT inferred from the PMUS
executor or from older Gemini code in this repo. That mattered: the
prediction-market schema is not the spot schema. Spot takes `amount` and an
`options: ["maker-or-cancel"]` array; prediction markets take `quantity`,
`outcome`, and a BOOLEAN `makerOrCancel`. Carrying the spot shape across would
have produced a request the venue rejects -- or worse, one it accepts with
post-only silently absent.

  POST /v1/prediction-markets/order          place
  POST /v1/prediction-markets/order/cancel   cancel, by orderId
  POST /v1/prediction-markets/orders/active  active orders, for reconciliation

QUANTITY AND PRICE ARE STRINGS. Price is in the 0-1 range. Sending floats
invites the representation drift that Decimal exists to prevent.

MAKER-ONLY IS `makerOrCancel: true`, a boolean, and NOT a timeInForce value.
timeInForce enumerates good-til-cancel (default), immediate-or-cancel and
fill-or-kill -- none of which mean post-only. If this field were mis-set the
order could cross and take, which is the one thing the canary envelope forbids
at the venue level.

The API key needs the NewOrder permission for the write paths and OrderStatus
for the reads; a key without them fails closed with an authorization error
rather than placing anything.

RE-VERIFIED 2026-09-18, after canary #1 was rejected with HTTP 400:

  * The fields go in a JSON REQUEST BODY with Content-Type: application/json.
    Every prediction-markets endpoint that takes fields documents them that
    way. This is NOT the classic Gemini private convention of an empty body
    with the JSON in X-GEMINI-PAYLOAD, and the difference is invisible until
    a request actually carries a field: the account reads we had already
    proven -- positions, settled positions -- take none.
  * Documented response codes are 201, 400, 401, 422, 500 and 503. A 400 is
    request validation. TERMS_NOT_ACCEPTED is a 422, so the rejection we
    received was NOT a terms problem.
  * timeInForce exists and enumerates good-til-cancel (default),
    immediate-or-cancel and fill-or-kill. makerOrCancel is a separate
    boolean defaulting to false. orderType also admits stop-limit.
  * The account WebSocket exposes a different order schema again
    (method order.place, with type/eventOutcome and an upper-case side).
    It is a second transport, not this one, and nothing here uses it.
"""
from __future__ import annotations

from typing import Final

PLACE_PATH: Final = "/v1/prediction-markets/order"
CANCEL_PATH: Final = "/v1/prediction-markets/order/cancel"
ACTIVE_PATH: Final = "/v1/prediction-markets/orders/active"

#: the only mutation paths this repository may reach
MUTATION_PATHS: Final = (PLACE_PATH, CANCEL_PATH)

ORDER_TYPE_LIMIT: Final = "limit"
SIDE_BUY: Final = "buy"
SIDE_SELL: Final = "sell"
OUTCOME_YES: Final = "yes"
OUTCOME_NO: Final = "no"

#: response `status` enum
STATUS_OPEN: Final = "open"
STATUS_FILLED: Final = "filled"
STATUS_CANCELLED: Final = "cancelled"
TERMINAL_STATUSES: Final = (STATUS_FILLED, STATUS_CANCELLED)

#: response fields we depend on
ORDER_ID_FIELD: Final = "orderId"
STATUS_FIELD: Final = "status"
FILLED_FIELD: Final = "filledQuantity"
REMAINING_FIELD: Final = "remainingQuantity"

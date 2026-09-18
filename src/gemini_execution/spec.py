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

  * The documented OrderRequest schema is symbol, orderType, side, quantity,
    price, outcome, stopPrice, timeInForce and makerOrCancel -- and NOTHING
    else. clientOrderId is in the RESPONSE schema only; canary #1 sent one.
  * The fields are documented as a JSON REQUEST BODY with Content-Type:
    application/json, and this venue ALSO reads them out of the classic
    signed X-GEMINI-PAYLOAD header (measured 2026-09-18 with a two-ticker
    read-only probe). We send both, so the request is valid either way. The
    shape was a hypothesis for the 400 and is now disproven: the fields
    arrived.
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
TIME_IN_FORCE_GTC: Final = "good-til-cancel"
SIDE_BUY: Final = "buy"
SIDE_SELL: Final = "sell"
OUTCOME_YES: Final = "yes"
OUTCOME_NO: Final = "no"

#: response `status` enum
STATUS_OPEN: Final = "open"
STATUS_FILLED: Final = "filled"
STATUS_CANCELLED: Final = "cancelled"
TERMINAL_STATUSES: Final = (STATUS_FILLED, STATUS_CANCELLED)

#: EXACTLY the fields the documented OrderRequest schema admits. clientOrderId
#: is NOT among them: it appears in the RESPONSE schema only. Canary #1 sent
#: one anyway, and a schema that rejects unknown properties answers 400 --
#: which is the status we received. Nothing may be added here because another
#: venue accepts it, or because a response carries it.
ORDER_REQUEST_FIELDS: Final = (
    "symbol", "orderType", "side", "quantity", "price", "outcome",
    "stopPrice", "timeInForce", "makerOrCancel",
)

#: the AUTH fields Gemini's signing protocol requires in the payload, and
#: nothing else. `request` is the path being signed; `nonce` guarantees
#: freshness. The API key, the signature and the secret travel in headers of
#: their own and never appear in the payload.
AUTH_PAYLOAD_FIELDS: Final = ("request", "nonce")

#: terminal-resolution fields on the CONTRACT object -- verified 2026-09-18
#: against four settled BTC5m contracts, all four self-consistent against
#: their own strike. status enumerates approved/active/closed/under_review/
#: settled/invalid.
RESOLUTION_FIELDS: Final = ("status", "resolutionSide", "resolvedAt",
                            "settlementValue")
STATUS_SETTLED: Final = "settled"

#: response fields we depend on
ORDER_ID_FIELD: Final = "orderId"
STATUS_FIELD: Final = "status"
FILLED_FIELD: Final = "filledQuantity"
REMAINING_FIELD: Final = "remainingQuantity"

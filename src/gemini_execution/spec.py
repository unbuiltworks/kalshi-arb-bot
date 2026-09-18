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

The API key needs the Trader role for these paths; a key without it fails
closed with an authorization error rather than placing anything.
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

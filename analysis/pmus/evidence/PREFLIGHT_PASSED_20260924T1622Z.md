# First complete PMUS preflight PASS

Deployment `d9460e5b` / commit `718be3f`, market
`cpc-btc-updown-15m-2026-09-24-1615z`, `2026-09-24T16:22:37.330802+00:00`.
Read-only; the deployment id was echoed back on every log call.

`"preflight": "PASSED"`, `"blocking_failures": []`.

Every gate, as reported:

| gate | passed | detail |
|---|---|---|
| credentials_present | true | env PMUS_API_KEY_ID / PMUS_API_SECRET_KEY present |
| transport_is_get_only | true | no order path exists in this process |
| account_readable | true | |
| account_binding_proven | true | fingerprint `d51b44a3fa83`, buying power 63.04895 |
| slug_is_btc15m_series | true | cpc-btc-updown-15m-2026-09-24-1615z |
| market_readable | true | |
| market_resolves | true | cpc-btc-updown-15m-2026-09-24-1615z |
| market_series_is_btc15m | true | |
| market_open_tradable | true | MARKET_STATUS_OPEN, active, not closed, not archived |
| outcome_mapping_is_up_down | true | contract long==Up AND venue YES==long |
| tick_constraint_known | true | **0.01** |
| quantity_constraint_known | true | **0.01** |
| book_readable | true | |
| book_readable_now | true | bid None / ask None |
| book_two_sided_at_preflight | **false** | NON-BLOCKING; binding check is at the decision point |
| positions_readable | true | |
| no_unexpected_open_orders | true | 0 |
| positions_reconciled | true | 0 venue positions |
| executor_starts_disarmed | true | no order path exists in this image |
| owner_arm_token_present | **false** | NON-BLOCKING; arming is a separate owner act |

Reads issued, and nothing else:
`GET /v1/account/balances`, `GET /v1/market/slug/{slug}`,
`GET /v1/markets/{slug}/book`, `GET /v1/portfolio/positions`,
`GET /v1/orders/open`.

## What this settles

`market_readable` PASSES on the AUTHENTICATED host. The public gateway
provably does not serve this series; the authenticated host does. Six gates
downstream of it -- series, open/tradable, outcome mapping, tick, quantity,
and the account/position gates -- had never once been evaluated, because
every previous window aborted before reaching them. They are evaluated here
and they pass.

Tick is 0.01, read from the market rather than assumed. That matters
against the 14:40Z preview evidence, where the venue accepted an off-tick
0.0150 and silently echoed 0.0100 instead of rejecting it: the constraint
now comes from the market resource, not from the venue's validation.

## What this does NOT settle

This preflight ran on a window the deploy landed inside (subscription
+235.7s, coverage 73.7%, all 47 L5 actions refused NO_BOOK). It says the
gates pass; it does not demonstrate subscription-at-open under this build.
That needs a window that ran end to end under `718be3f`.

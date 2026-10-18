# The cancel HTTP 400: cause, correction, evidence

Read-only diagnosis. No order submitted, no experiment created, no fix
deployed. Shadow lock ON, mechanism OFF, HALT latch preserved.

## 1. Outcome of the authorized test

| item | value |
|---|---|
| venue order id | **`D68KZ0SG10JR`** |
| placed | 2026-10-18T23:20:52.85Z, market `cpc-btc-updown-15m-2026-10-18-2315z` |
| order | UP, MAKER, `post_only: true`, qty **0.01** @ **0.6400** (bid 0.66 / ask 0.67) |
| authoritative terminal record | 23:31:22.064Z, `state: ORDER_STATE_EXPIRED`, `terminal: true`, `released: true` |
| cumulative fills | **`cum_qty: 0`**, `avg_px: 0.0000` |
| fees | **`commission: 0.0000`** |
| what removed it | **market EXPIRY at 23:30:00Z** — not a cancellation, ours or the venue's |
| gross outflow | **$0.00** |
| independent cancellation | **NOT PROVEN** (`independent_cancellation_proven: false`) |

The watchdog detected the order overdue at age 308.0s and attempted
cancellation repeatedly to age 530.7s; every attempt returned
`cancel_error: HTTPError` with `cancelled: []`. The executor's own backstop
then fired at `rest_age_s 401.5` and failed identically:

```
self_cancel_why   = "WATCHDOG_DID_NOT_CANCEL_IN_TIME"
self_cancel_error = "HTTPError: HTTP Error 400: Bad Request"
cancelled_by      = "SELF_FALLBACK"
venue_terminal    = false
```

## 2. Exact cause: the cancel body omits the required `marketSlug`

All three of our cancel call sites post an EMPTY body. The historically
working execution stack posts the market slug.

| caller | request |
|---|---|
| `l5_pmus_transport.cancel` (armed path, line 1280) | `POST /v1/order/{id}/cancel` body **`{}`** |
| `l5_pmus_transport.cancel` (oracle path, line 1295) | `POST /v1/order/{id}/cancel` body **`{}`** |
| `l5_watchdog.Http.cancel` (line 188) | `POST /v1/order/{id}/cancel` body **`{}`** |
| **`pmus_q1_executor.cancel_exact` (line 430)** | `POST /v1/order/{order_id}/cancel` body **`{"marketSlug": slug}`** |

Endpoint, HTTP method, identifier type (the venue's own order id from
submit, not our client order id) and the signer are **identical** across all
four. The single difference is the presence of `marketSlug` in the body.

`pmus_q1_executor` is the stack our own preflight calls "the working
execution stack", and its `cancel_exact` is the only cancel implementation
in this repository that has ever been used against live PMUS orders.

## 3. Evidence that this is the cause

1. **Deterministic, not intermittent.** Repeated attempts across ~222
   seconds and 34 venue re-reads returned the same 400. A transient, an
   auth problem or a rate limit would not be stable.
2. **The status code is the right shape.** 400 Bad Request is a schema
   rejection. Authentication failure is 401/403, an unknown id is 404, rate
   limiting is 429 — and the same signer, credentials and path produced
   successful GET reads of this very order id throughout (the 34 re-reads
   that established `ORDER_STATE_NEW` then `ORDER_STATE_EXPIRED`). So the
   credential, the signature and the id were all accepted; only the cancel
   POST was refused.
3. **Both independent cancellers failed the same way.** The watchdog
   (separate service, separate process, separate HTTP class) and the
   executor's backstop both send `{}` and both got 400. A defect in one
   process would not reproduce in the other; a shared wrong body does.
4. **The venue's order API is slug-scoped throughout.** Submit builds its
   body as `OB.from_intent(intent, self.market_slug)`, and the account-wide
   deadman is `POST /v1/orders/open/cancel {"slugs": [slug]}` — also keyed
   by slug. A slug-keyed single-order cancel is consistent with that shape.

## 4. What I could NOT capture, and why

**The sanitized venue response body is not available.** Nothing in the
stack reads it: `urllib`'s `HTTPError` stringifies to exactly
`"HTTP Error 400: Bad Request"` and the body is never consumed. The
transport already documents this limitation at line ~1180 for the submit
path ("neither carries the path, so the exception alone cannot tell a
failed /v1/order/preview from a failed /v1/orders").

So section 2 is an inference from a code diff against the working stack,
not a quotation of the venue telling us which field is missing. It is a
strong inference, but it is an inference, and I am labelling it as one.

## 5. Smallest correction

**One field, three call sites.** Add `marketSlug` to the cancel body:

- `l5_pmus_transport.cancel`: the slug is already on the instance as
  `self.market_slug`; both `_request` calls become
  `{"marketSlug": self.market_slug}`. Refuse rather than send an empty slug,
  since an absent slug would reproduce the present failure silently.
- `l5_watchdog.Http.cancel`: takes the slug as an argument. The caller
  already has it — every sweep row carries `market`, e.g.
  `{"venue_order_id": "D68KZ0SG10JR", "market": "cpc-btc-updown-15m-2026-10-18-2315z"}`.
  The route regex `CANCEL_ROUTE` is unchanged; only the body changes.

The signature covers method, path and body, so the signed payload changes
with it — handled by the existing signer, which the watchdog's own comment
at line ~156 already notes ("a cancel signed over `{}`+POST+path").

## 6. Recommended order of work, and what I am NOT doing

**First, capture the response body — observability only, no behaviour
change.** Read and record a truncated, credential-free body on `HTTPError`
in the cancel path. That turns section 2 from an inference into a
quotation: the next 400 would name the missing field. This is the only step
that can CONFIRM the cause rather than infer it.

**Then** apply the one-field correction, with a test asserting the cancel
body carries a non-empty `marketSlug` at all three sites and that an absent
slug refuses rather than posts `{}`.

**Only then** would another live cancellation attempt be worth an
authorization — and that needs a fresh session id, since
`sub1.MECH-CANCEL-20261018T1743Z-1438AF` is spent.

I have deployed nothing, submitted nothing, and created no new experiment.

## 7. Risk state as of 2026-10-18T23:32:36Z

Deployment `b17a5b57` SUCCESS:

- `L5_LIVE_SHADOW_LOCK {"locked": true, "blocked": ["submit"]}`
- mechanism **OFF** (no `L5_MECH_MODE` emitted)
- `auth_not_after: {"state": "EXPIRED", "expires_in_s": -156.2}` — the
  authorization has lapsed on its own, a second independent block
- limits unchanged: per-market $5, exposure $5, residual 10, concurrency 1,
  live orders 1, daily halt $5
- **HALT still latched** (`UNMANAGED_RESTING_ORDER`), and the watchdog is
  refusing to clear it: *"refusing to overwrite a latched HALT: clearing is
  an owner action, not an automated one"*
- watchdog live, `cancel_enabled: true`, `open_orders: 0`, `read_error: null`
- October 14 claim lock untouched; one-shot claim spent, not reset

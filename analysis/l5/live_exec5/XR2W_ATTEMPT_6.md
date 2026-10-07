# LIVE-L5-EXEC-1-XR2W — Attempt 6

**FILLED. The first evidenced fill from our own order path.**

| field | value |
|---|---|
| classification | **SUBMITTED → RESTED → FILLED.** Not settled. Not realized. |
| fingerprint | `ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562` |
| deployment | `84f61d1b-3fb9-4395-a6b2-89597966e66a` |
| SHA | `d61235fb0c86f79b47c450125125ee9dc2c57fc9` (the authorized SHA) |
| slug | `cpc-btc-updown-15m-2026-10-07-0200z` |
| session | `xr2w-a6-2026-10-07-3095633d` |
| order id | **`CYKE4XSQ0YGW`** |
| started / ended | 00:17:30Z → 02:01:05Z |
| orders submitted | **1** (max 1) |
| matched-pair economics | **N/A** — single-leg experiment. Not zero; not applicable. |

## The required path, every step evidenced

**AuthBook → decision → preflight → canonical preview → preview accepted →
TTE → durable latch → TTE → POST /v1/orders → venue response**

1. **Pre-open wait:** 1,230 polls over **6,150.1 s** (1 h 42 m) for that
   slug and no other. Window-open transition observed at **TTE 899.508 s**.
2. **TTE gate 1 (run start):** allowed, 899.508 s, cutoff 120.0 s.
3. **AuthBook:** two-sided, age **0.084 s** against the 2.0 s bound, first
   try. Source `l5_pmus_auth_book.AuthBook`.
4. **Side:** frozen before the window — digest
   `c18fcee5966215d65b8c87a2d114eb8c22c7ab15ee3d75202bde0f243c1c74aa`,
   low bit 1 → **DOWN / `OUTCOME_SIDE_NO`**. Not a forecast.
5. **Decision at 02:00:00Z, TTE 899.464 s.**
6. **Preflight:** ok, **zero refusals**. Armed, gate asserted for the slug,
   size pass, book ok, latch available and unspent.
7. **Canonical preview** `{"request": body}` → **ACCEPTED**, echo ok, zero
   mismatches. `participateDontInitiate` is recorded as *not echoed* and
   checked by consequence: a maker order showing filled quantity in
   preview would not have rested.
8. **TTE gates 2 and 3:** `PRE_LATCH_AND_PRE_POST` — **both rechecks
   passed**. This is XR2's correction doing its job on a live order.
9. **Durable latch consumed** 02:00:00.5798Z, before the POST, at
   `/state/pmus-ws/exec1x/xr2w-a6-2026-10-07-3095633d.aa9e0544dbf67ab9.submitted`.
10. **`POST /v1/orders`** → `{"id": "CYKE4XSQ0YGW", "executions": []}`.

## DOWN: semantic price and wire representation

| | |
|---|---|
| semantic side | DOWN |
| semantic own-side price | **0.6700** (in DOWN's own terms) |
| venue YES bid / ask | 0.3200 / 0.3300 |
| own-side touch | **0.6700** = 1 − 0.3300 |
| own-side opposite touch | 0.6800 |
| spread | 0.0100 |
| offset from touch | **0 ticks** — passive, never crossing |
| wire `outcomeSide` | `OUTCOME_SIDE_NO` |
| wire `action` | `ORDER_ACTION_BUY` |
| wire `price.value` | **0.3300** = 1 − 0.6700 |
| wire `price.currency` | `USD` |
| wire `quantity` | **0.01** as a JSON number |
| anonymous depth ahead | 200.0000 — **NOT our queue position** |

The complement chain the A5 rejection forced us to pin is now confirmed
against a venue that **accepted** the body.

## Lifecycle

- `ORDER_STATE_NEW` from **02:00:01Z**, `leavesQuantity` 0.01, through 25
  consecutive polls.
- **`ORDER_STATE_FILLED` at 02:00:15.69Z**, `cumQuantity` **0.01**,
  `leavesQuantity` 0.
- **Time from submission to fill: ≈ 15.2 s.** Observation ran
  `rest_time_s` 65.224 s.
- One `read_error: VenueHTTPError` at 02:00:01Z — the new error capture
  catching a transient read, non-blocking.

## Fill economics, labelled

- **filled quantity 0.01**, order state FILLED
- **fees_usd 0**
- **venue-confirmed liquidity role: UNRESOLVED.** `n_executions` 0 and
  **no `isAggressor` on any execution**. The bare boolean `aggressor` was
  not read. Recorded as `unresolved_role_fills` with the quantity kept and
  **no maker claim invented** — which matters, because post-only is
  *evidence of intent, not proof of maker*.
- directional residual **0.01 shares**; residual P&L
  `UNRESOLVED_UNTIL_SETTLEMENT`
- credited rewards **$0**
- final net `UNRESOLVED_UNTIL_CASH_CONFIRMED`

**Markouts** on `observed_best_bid`, labelled
`MARK_TO_MARKET_NOT_REALIZED`, fill price 0.6700:

| horizon | mark | markout |
|---|---|---|
| +1 s | 0.7200 | +0.0500 |
| +5 s | 0.7600 | +0.0900 |
| +10 s | 0.7300 | +0.0600 |
| +30 s | 0.7000 | +0.0300 |
| +60 s | 0.7400 | +0.0700 |
| settlement | — | `NOT_SETTLED` |

All five horizons elapsed, none missing, none interpolated. **Every one is
favourable — and every one is mark-to-market on a single 0.01 share.**
That is not profit and not evidence of edge.

## Deadman and open-order state

`cancel_all` → `{"canceledOrderIds": []}` — nothing to cancel, correct,
because the order was **fully filled** with `leavesQuantity` 0. There is
**no unresolved or open Attempt-6 order**. What remains is a **0.01-share
DOWN position** awaiting settlement at the window close.

## Disarmed

All five owner values set empty with `skipDeploys: true`, so nothing
redeploys and nothing relaunches. Latch spent and never released. No
second order, no replacement, no complement, no Attempt 7.

## What this does and does not establish

**Establishes:** the corrected wire body is accepted by the venue; a
post-only 0.01-share order at the own-side touch **rests and fills** on
PMUS BTC15m; all three TTE gates, the preview echo, the durable latch and
the deadman work on a live order; the DOWN complement mapping is right.

**Does not establish:** that we earn the maker rebate — the role is
**UNRESOLVED**, which is exactly the variable the incentive economics turn
on. Nor any edge: one fill, 15 s, $0.0067 of notional, favourable marks
that are not realized. `DOES_NOT_TEST_FULL_L5_ECONOMICS` is true.

The open item is settlement and cash confirmation, plus whether a maker
rebate is credited against this fill. Until both, nothing here is realized.

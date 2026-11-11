# CONTINUOUS-4 settlement, and the observability defect that hid it

`GEMI-BTC05M2609181730-UP` resolved at **2026-09-18T17:30:07.369Z** — seven
seconds after expiry. Our probe reported it unsettled at 17:31 and again at
17:55. The Gemini UI showed "Resolved — Outcome has occurred and payouts have
been distributed" the whole time.

**No execution or controller hash changed.** Lineage preserved:
`executable 4d9f2e4c…`, `bundle a902bd4a…`, `envelope b3bff654…` unchanged.
Neither settlement module is a bundle member.

## Accounting

| stage | value |
|---|---|
| **SUBMITTED** | 5 `order.place` + 4 `order.cancel`, all ack 200 |
| **FILLED** | YES 0.01 @ 0.92, NO 0.01 @ 0.14 — `/v1/mytrades` rows 2, position `0.02` |
| **SETTLED** | **YES.** `status: settled`, `resolutionSide: "yes"`, `resolvedAt 17:30:07.369Z`, 3/3 reads agree, no flap, strike cross-check agrees |
| **REALIZED** | **still $0.00** — `settlement_cashflow` absent; no attributable cash movement found |

Reconstruction, now from venue evidence end to end:

```
cost    -0.0106   (0.01 × 0.92  +  0.01 × 0.14)
payout  +0.0100   (YES redeems 1, NO redeems 0, since the contract resolved yes)
fees     0
        -------
P&L     -0.0006   EXPECTED — arithmetic on our records, not a cash fact
```

This matches the expected matched-pair economics exactly. It is
`reconstructed_cash`, a **cross-check**, not `realized`. `realized()` still
raises `NotSettled` and the gate still reads `BLOCKED`.

## Root cause

**Nothing ever assigned `settlement_value` or `settlement_source`.** The proof
chain's `authoritative_outcome` link evaluated `bool(None and None)` — `False`
for every symbol, always. A resolved contract could stay labelled unsettled
indefinitely.

It was **never a schema problem**. The contract object is PUBLIC and had
carried everything needed since 17:30:07Z, and `metadata.resolution_for`
already parsed it *including* a strike cross-check and flap handling. The
settlement probe simply never called it.

### Two further defects, latent behind the first

Both would have produced a **wrong number** rather than a missing one.

**The venue's `settlementValue` is the underlying index print, not a payout.**
Captured: `"80727.9451111111"` against a strike of `"80694.84"`. The dataclass
comment said `"0" or "1"`. Wiring the field through would have valued a $0.01
position at roughly $807.

**`Leg` carried no outcome**, so YES and NO legs were indistinguishable and
`reconstructed()` multiplied the whole position by one settlement value. For
this position that pays out *both* units: **+0.0094 — a phantom profit with
the sign inverted**, where the truth is −0.0006.

## Captured machine-readable evidence

`analysis/gemini/venue_resolution_shape_20260918.json`, verbatim:

```json
"status": "settled",  "marketState": "closed",
"resolutionSide": "yes",
"resolvedAt": "2026-09-18T17:30:07.369Z",
"settlementValue": "80727.9451111111",
"strike": {"value": "80694.84"}
```

Source: `/v1/prediction-markets/events/{eventTicker}` contract object, PUBLIC.
`strike_check.agrees: true` — the published side is independently implied by
`settlementValue >= strike.value`.

**`/v1/mytrades` cannot name the side.** Row keys captured live:
`aggressor, amount, client_order_id, exchange, fee_amount, fee_currency,
is_auction_fill, is_clearing_fill, order_id, price, symbol, tid, timestamp,
timestampms, type` — no `outcome`. The side is joined from **order history on
`orderId`**, where the venue publishes its own `outcome`. Deliberately *not*
parsed from our `clientOrderId` (which spells `…-yes-…`/`…-no-…`): that string
is our label, the venue's field is evidence.

## Why REALIZED is still $0.00

`settlement_cashflow` is the only remaining break in the chain, and it is a
genuine absence rather than another blind spot:

- `/v1/settlements` → **404**
- `/v1/prediction-markets/settlements` → **404**
- `/v1/prediction-markets/positions` → 200, **0 rows** (consistent with the
  position having been settled and removed, but not a cashflow record)
- `/v1/mytrades` → the two fills only; no settlement/redemption row

A balance delta without source identity is not attribution, and the UI's
rounded `$0.01`/`$0.00` display is not sub-cent cash accounting. **−$0.0006
stays EXPECTED until an attributable cash movement is found.**

## Fixed so this cannot recur

- the probe reads the outcome and the report surfaces `resolution_side`,
  `resolved_at`, `underlying_settlement_price` and `payout`
- an open position whose legs cannot name their side reconstructs to `None`,
  never to a number
- a net-zero position still reconstructs without labelled legs — the one case
  where the old arithmetic was provably right
- a non-SETTLED reading attaches nothing, so a flapping endpoint can never
  record a contract as un-settled
- **17 regression tests** built on the captured shapes

The repo-wide write-path isolation guard was **narrowed in the open**, as its
own docstring requires: exactly one read-only module (`metadata`) may be
imported from the execution package, guarded by a new test asserting that
module carries no mutation surface. The guard was made **stricter** in the same
change — it now resolves full dotted import names, so
`from src.gemini_execution import ws_session` is caught even when smuggled in
alongside `metadata`, which the old package-prefix check would have waved
through.

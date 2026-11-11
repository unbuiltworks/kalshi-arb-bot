# CONTINUOUS-3: every gate opened, then the driver refused on its own ledger

`OWNER-2026-09-18-GEMINI-CONTINUOUS-3`, run 35367159161, armed step
16:15:37 -> 16:15:49Z, head `d48c8d16`.

**NO ORDER WAS SUBMITTED. NO FRAME REACHED THE SOCKET. THE WIRE LOG IS EMPTY.
REALIZED $0.00. No new exposure of any kind.**

| | |
|---|---|
| executable_sha | `842685116fb95c7917b5ec286c12ee3d3da61ccc76d8560069b89e6c9b506ad4` (as authorized) |
| envelope_sha | `b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17` (unchanged) |
| terminal state | `SUBMISSION_UNKNOWN_RECONCILE_REQUIRED` |
| mutation slots used | 0 of 12 |
| cycles | 0 |
| authorization | SPENT |

## What happened, in order

1. Symbol `GEMI-BTC05M2609181620-UP`, TTE 262.8s, active/open -- inside the band.
2. Fresh authoritative preflight AGREED with the frozen assumptions, zero
   discrepancies, `terms_accepted: true`. Checked BEFORE the latch.
3. **Authorization passed for the first time**: `authorized: true`, dual key
   armed, `executable_sha == AUTHORIZED_SHA`, `envelope_sha ==
   AUTHORIZED_ENVELOPE`. The gate is reachable; it has never opened before.
4. Latch consumed -- `INTENT_PERSISTED`, `venue_order_id: null`.
5. Session connected.
6. `Driver.recover()` replayed the **persistent** mutation ledger and found one
   attempt with no recorded outcome. It stopped without sending anything.

The blocking row is from the PREVIOUS window:

```
CANCEL slot 2  auth OWNER-2026-09-18-GEMINI-CONTINUOUS-2
               symbol GEMI-BTC05M2609181435-UP
               clientOrderId l5cp-owner-2026-yes-774c492b
RESOLVE slot 2 outcome UNKNOWN  status 400
               ack {"error": {"code": -1013, "msg": "invalid orderId format"}}
```

That is the cancel that used `clientOrderId` where the venue wanted `orderId`.
The seam was fixed; **the ledger row it left behind was not**, and the ledger
is committed to the repo, so it survives into every later window.

**This is the ambiguity rule working.** The driver will not send a new mutation
while a previous one has no recorded outcome. It refused for exactly the reason
it was built to refuse.

## Two findings worth acting on

### 1. A post-latch READ refusal spent the authorization

The latch ordering fix moved the preflight before the latch, so a preflight
refusal can no longer cost an authorization. `recover()` was not moved, and it
has the same character: it is a **read** -- of the on-disk ledger and of venue
state -- that can refuse **deterministically, before anything is sent**.

CONTINUOUS-1 was lost to a fail-closed preflight after the latch. CONTINUOUS-3
was lost to a fail-closed ledger reconcile after the latch. Same family, one
step further down the path.

The reconcile check could not simply be moved the way the preflight was:
`recover()` lives inside `run_window`, which is covered by the authorized
`executable_sha`, while the latch is consumed in the invoker. A fix is
available without touching the hashed driver -- the invoker can call
`recover()` as a read BEFORE consuming the latch and refuse there -- but it
needs care, because `recover()` also seeds inventory and feeds
`reconstruct_budget`, so calling it twice must not double-count. **Not done: it
is an execution-path change and this window was authorized with none.**

### 2. `UNKNOWN` may be too strong for a definitive venue rejection

The venue answered `400` with `-1013 invalid orderId format`. That is not an
ambiguous outcome -- it is the venue stating it refused to act on a malformed
request. The order demonstrably still existed afterwards, which is what the
error means.

For a PLACE, treating any non-200 as UNKNOWN is right: a rejected or timed-out
place can still leave an order resting. For a CANCEL the venue rejected on
FORMAT, nothing changed and the outcome is knowable.

Because `UNKNOWN` is sticky and blocks every future window, that conservatism
has a real cost. **Flagged, not changed** -- it is a driver change, and the
authorization named this driver.

## The place-ack capture: built and exercised, still not proven

The capture is deployed and ran on the production path. It recorded nothing,
because nothing was sent. `place_acks_observed: []`, `wire: []`.

**The successful `order.place` ack shape therefore remains INFERRED.**
`analysis/gemini/venue_shapes_20260918.json` still carries `"CAPTURED": false`
and it stays that way until a live ack lands.

What IS proven: the capture sits at the session seam the invoker owns, so
`executable_sha` is byte-identical to the authorized hash -- verified before
arming and again in the run report.

## Accounting

Strict labels. Nothing below is realized profit.

| stage | value |
|---|---|
| submitted | 0 orders |
| filled | 0 |
| settled | none |
| **realized** | **$0.00** |
| peak cash committed | $0.00 |
| matched-pair | $0.00 |
| directional residual | $0.00 |
| fees | $0.00 |
| rebates/rewards | none credited, none assumed |

Account state on the window's symbol, before and after: `fills []`,
`open_orders []`, order history `NO_ORDERS_ON_RECORD`, `total_rows 0`.

## Authorizations now spent

| id | outcome |
|---|---|
| `OWNER-2026-09-17-GEMINI-CANARY-1` | REJECTED |
| `OWNER-2026-09-18-GEMINI-CONTINUOUS-1` | preflight refused after latch; 0 orders |
| `OWNER-2026-09-18-GEMINI-CONTINUOUS-2` | 2 orders accepted, 0 filled, $0.00 |
| `OWNER-2026-09-18-GEMINI-CONTINUOUS-3` | ledger reconcile refused after latch; 0 orders |

## Precondition for any CONTINUOUS-4

A new authorization would hit the same wall on the same ledger row. Before
another window is worth granting, slot 2 must be resolved against the venue by
`clientOrderId l5cp-owner-2026-yes-774c492b` and a RESOLVE row written.

The venue evidence needed already exists in part: at 14:31:11Z both orders were
`status: open`, `filledQuantity 0`; after expiry `/v1/mytrades` returned 0 rows,
`position "0"`, `fills_cash "0"`. The cancel never applied and the order never
filled.

Writing that RESOLVE row clears a safety block, so it is not something to do
unilaterally. It is proposed, not done.

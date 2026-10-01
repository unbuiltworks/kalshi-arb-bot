# Case-1 live calibration, 2026-10-01: PROBE_FAILURE. Case 1 remains unresolved.

**Verdict `PROBE_FAILURE`. Realized P&L $0.00. No position was ever held.**
The probe exposed that the calibration INSTRUMENT was untrustworthy, so it
demonstrated nothing about the venue. This is not a Case-1 answer and must never
be cited as one.

## What ran

| | |
|---|---|
| order | `CV4B6ZW7AYGE` |
| market | `cpc-btc-updown-15m-2026-10-01-1630z` (close 16:45:00Z) |
| side / price | `down` (NO), own-terms **0.1400**, wire `price.value 0.86` |
| quantity | 1, post-only GTC, `participateDontInitiate` |
| submitted | 16:37:53.433Z |
| observed | 336.8 s, stopped by `WINDOW_TAIL_GUARD` |
| deployment | `49ed19e8`, commit `17bd9572`, digest `59c0d581` |
| worst-case loss | $0.14 |

Public evidence captured: 6,912 frames, 3,421 book updates, 500 trades.

## Economic outcome: nothing happened, and that is certain

From the venue's own private order stream:

```
16:37:53.433  EXECUTION_TYPE_NEW       ORDER_STATE_NEW       cum 0  leaves 1
16:43:30.335  EXECUTION_TYPE_CANCELED  ORDER_STATE_CANCELED  cum 0  leaves 0
                                       commissionNotionalCollected 0.0000
```

`cumQuantity 0` on every frame: no contract was ever acquired, so there is
nothing to settle and no residual exposure. `leavesQuantity 0`: nothing remains
working. The market then closed.

## WHY IT IS A FAILURE: the entry was priced off a stale feed

| source | YES book | own-terms DOWN touch |
|---|---|---|
| selector, 16:32:57Z | 0.74 / 0.75 | 0.25 |
| probe preflight, REST gateway, 16:37:52Z | **0.84 / 0.85** | 0.15 -> entry **0.14** |
| probe's own authenticated WS book, seconds later | **0.65 / 0.66** | **0.34** |

`X.fresh_bbo` reads `gateway.polymarket.us`, which this repository's own recorder
documents as roughly 30 seconds stale. It disagreed with the authenticated WS book
by **19 cents** at the same instant.

The preregistered rule is "rest ONE tick below the own-terms touch". We actually
rested about **TWENTY ticks** below it. The book then moved further away
(YES 0.85 -> 0.65 -> 0.54, so our DOWN leg's own ask rose to 0.46).
**No market direction could have produced a Case-1 observation from this order.**
The null is an instrument artifact, not evidence.

Correctly, the probe did NOT use the selector's stale 0.24: it recomputed from its
own fresh read. The defect is that "fresh" meant the cached gateway.

## The reconciliation gap

`cancel_and_resolve` raised
`CancelUnconfirmed: still not terminal after 6 reads`, so `terminal_proved` and
`sweep_verified` stayed false and the probe refused to self-certify -- the design
working. REST disagreed with the private stream, which had already shown
`ORDER_STATE_CANCELED`.

**An independent REST confirmation of zero open orders was NOT obtained.** Three
attempts to run the read-only auditor failed because Railway's `redeploy` reuses
the prior deployment's start command and kept re-running the (disarmed, refusing)
probe. Recorded as an open item rather than claimed as proof. The economic risk is
nil because `cumQuantity` was 0 throughout.

## Three defects found, RECORDED AND NOT FIXED

1. **BBO source.** Price the entry from the authenticated WS book, not the cached
   gateway. This alone invalidated the observation.
2. **`aggressor` is a BOOLEAN**, not an enum string: the venue sent
   `"aggressor": false`. `role_from_aggressor` parses for `BUY`/`SELL` substrings
   and would never have resolved a role. The two-signal rule caught this as
   `UNRESOLVED` instead of producing a confident wrong answer.
3. **`extract_private` skips `Mapping` values**, so `lastPx` and
   `commissionNotionalCollected` were reported `UNAVAILABLE` when both were
   present as `{"value": "0.0000"}`.

All three must be fixed before any Case-1 retry. **No retry is authorized, and
none runs automatically.**

## What DID work, and is now established

* The narrow authorization gate ran **in production**: the relocated artifact was
  present, its code digest matched inside the container, the TTL held, and the
  scoped HALT override was accepted -- all before `armed()`.
* Disarmed smoke refused correctly twice, exiting nonzero both times.
* The arming contract held: a disarmed service placed nothing.
* The serializer's NO inversion is confirmed live: own-terms 0.14 went out as
  `price.value 0.86`, `outcomeSide OUTCOME_SIDE_NO`,
  `intent ORDER_INTENT_BUY_SHORT`.
* The private socket delivers `EXECUTION_TYPE_NEW` and `EXECUTION_TYPE_CANCELED`
  for our own order within ~30 ms of the venue timestamp.
* `tradeId` was `""` on both NEW and CANCELED, consistent with the schema run.
  Whether a FILL populates it is still unestablished.

## Consequence for replay

Case 1 stays unresolved, so replay takes the CONSERVATIVE treatment:
`CONSERVATIVE_CASE1 = True` in `src/l5_two_sided.py`. A print that would fill a
resting leg whose price the contemporaneous book has CROSSED grants **no fill**
and is counted in `case1_unresolved_no_fill` / `case1_unresolved_qty`. We do not
grant ourselves a favourable fill we have not demonstrated.

# Release: the two-sided execution lifecycle

One deployment for the whole batch, per `analysis/l5/RELEASE_GATE.md`.

## Gate

| check | result |
|---|---|
| targeted suites | 213 passed (probe, wiring, lifecycle pricing, one-order limit, E2E) |
| full regression | 26 failed / 11,790 passed / 14 errors = the SAME 40 baseline IDs, **zero NEW, zero GONE**, run completed |
| end-to-end window test | 28 passed, production configuration |
| production configuration | derived from records, not assumed: `depth_ticks=2`, `cancel_margin=300`, `completion_cross` unset, `max_live_orders=1`, `$4.65`/`$5`/`$5`, `NO_RECYCLING` |
| shadow lock + safety | verified on the running process after deploy |
| expected improvement | below, written BEFORE the deploy |

## What is in it

1. **B1** the one-live-order bound survives a partial fill (`PARTIALLY_FILLED`
   contains `FILLED`).
2. **B2** a leg cancelled unfilled is re-placed within its attempt budget
   instead of the engine switching sides.
3. **B4** a round whose plan the book has left is RETIRED -- and only with
   nothing live, nothing still fillable and no filled quantity on either leg,
   including a partial. Claims are never deleted; retirement is an additive
   durable fact that makes `probe_round` walk past.
4. **B3** on an actual first-leg fill the complement is derived from the
   CURRENT authenticated book and the UNMATCHED remainder, priced against the
   cost actually paid. A complement that no longer clears the fee floor or the
   pair cap is refused, not repriced into a loss.
5. **C3** a hard pair-cost cap at the 0.96 spring target, on both the opening
   pair and the complement, so the discipline is a gate rather than a side
   effect of `L5_TS_DEPTH_TICKS`.
6. **Item 4** the side/price transformation is now one function with tests:
   `own + wire == 1` for DOWN, BID for UP and ASK for DOWN, and crossing
   decided on the WIRE price. The 0330z plan's DOWN 0.5400 and its queue
   row's wire 0.4600 are reconciled as the same order.
7. The book is resolved ONCE for the two-sided branch (`ts_book`) and
   `authenticated` is still never reassigned, so the L5-following translation
   is untouched.

## Expected improvement, falsifiable in one window

* **`TWO_SIDED_ROUND` records appear with `retired: true`** in any window
  where BTC moves enough to strand a plan, each naming
  `plan_unexecutable` (`LEG_WOULD_CROSS:<side>` or
  `PRICE_LEFT_BEHIND:<side>:<n>_ticks`).
* **More than one distinct proposed pair price per moving window.** 0330z
  proposed ONE price 32 times; a window with a comparable move should now
  re-plan at the new touch.
* **No decision carries a `pair_cost` above 0.96**, at any `depth_ticks`.
* **`TWO_SIDED_COMPLEMENT` records appear only where one side is ahead**, and
  never with `provenance` other than `DERIVED_FROM_CURRENT_BOOK`.

If a window shows none of these and BTC did move, the retirement path is not
reached and that is the thing to fix -- not another research cycle.

## What this release does NOT do

It authorizes nothing. `SHADOW_ONLY_LOCK` stays engaged, the authorization
stays EXPIRED, the $5 envelope and every independent protection are unchanged,
and the watchdog is untouched. Under the shadow lock the submission claim is
never taken, so the complement, round advancement and settlement paths are
exercised by the end-to-end test rather than by production.

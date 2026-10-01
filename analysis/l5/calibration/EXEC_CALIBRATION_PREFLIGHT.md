# Execution-calibration campaign — PREFLIGHT ONLY, no authorization requested

Accounting-repair checkpoint `8784bfdc`. **STOP AT PREFLIGHT.** Nothing here is
armed; no live authorization is being sought in this document.

## Why a live campaign rather than more replay

The two-order ground-truth corpus does **NOT** validate crossed-resting
semantics, and I will not claim otherwise. Our only real maker was a BUY at
0.3500 and the book moved **up** to 0.43/0.44, so our bid fell BEHIND the touch
and was never reached from above. The historical tape therefore cannot settle the
one question driving V3's artifact:

> **What does PMUS actually do to a previously resting q1 maker order when the
> market subsequently reaches or moves through its price?**

Every other live probe predates recorder coverage (09-16/09-17 vs coverage from
2026-09-27 03Z), so no additional tape-backed lifecycle exists.

## This is a SEMANTICS campaign, not a profit probe

Success = we learned venue behaviour. Success is NOT positive P&L, and a
qualifying observation that loses money is a SUCCESS. No arm, cap, clip or policy
is being tested, and no economic conclusion will be drawn from the result.

**We do not manufacture the condition.** No intentionally marketable order is
ever submitted to create a crossed state -- that would study a different
phenomenon (submission semantics, which the q1 pair already evidences). We place
a legitimate passive maker and WAIT to see whether the market comes to it.

## Exact q=1 behaviour

1. One market at a time, BTC15m, selected by the existing discovery path.
2. **One** outstanding order, ever. `quantity = 1`. `clip` is not a parameter
   here and is not increased.
3. Maker submission only, at or inside the touch, **never marketable**:
   `post_only=True` with `TIF_GTC`, plus the existing pre-submit
   `X.would_cross(...)` guard. A post-only rejection is EVIDENCE and is recorded,
   not retried at a worse price.
4. **Entry price cap 0.50**, so a single unhedged contract cannot lose more than
   $0.50 at settlement.
5. After submission the harness does nothing but OBSERVE. It does not requote, it
   does not chase, it does not cancel on an adverse move -- repricing would
   destroy the exact observation we need.
6. If the maker fills, the harness attempts ONE fee-safe complement using the
   proven reactive-take path (`l5_order_body.build` + `preview_envelope`,
   `TIF_IOC`, `post_only=False`). If no fee-safe complement exists before the
   sweep deadline, the single contract is carried to settlement; that is the
   bounded loss case.
7. **Case B (level persistence)** is attempted only if a fill occurs naturally:
   after a fill, replenish ONCE at the SAME price and observe. It is never
   attempted by forcing a fill.

## Bounds

| bound | value |
|---|---|
| outstanding orders | **1** |
| quantity per order | **1** |
| maker entry price cap | **0.50** |
| maker submissions, campaign total | **6** |
| windows, campaign total | **6** |
| **cumulative realized loss cap** | **$1.50** |
| concurrent markets | 1 |
| size / residual-cap changes | **none** |

Worst case is bounded by the loss cap, which is checked before every submission
using the existing `worst_case_loss()` accounting, not after.

## Immediate-stop conditions

The campaign halts, sweeps and exits on the FIRST of:

1. **2 qualifying observations captured** (the success condition);
2. cumulative realized loss reaching **$1.50**;
3. 6 maker submissions or 6 windows consumed;
4. any order whose terminal state cannot be positively proven after
   `CANCEL_CONFIRM_POLLS` reads -- `UNKNOWN` halts, as in the existing canary;
5. feed staleness (`StaleFeed`) or WS disconnect beyond the existing threshold;
6. any venue error outside the known-classified set;
7. clock within `SWEEP_BUFFER_S = 90`s of window end -- sweep, verify
   `sweep_verified`, then stop;
8. any invariant raise from `_dispose()` or the lifecycle classifier.

Deadman: the existing cancel-all-and-prove-zero-open-orders sweep runs before any
terminal report, and the service keeps restart policy **NEVER** so a crash or
completion cannot relaunch it.

## Evidence captured per lifecycle, finest timestamps available

* submission: intended price/side/qty, `post_only`, our monotonic + wall clock,
  the full request body, and the preview response;
* venue ACK: order id, accepted price/qty, state, server timestamps;
* private order events: every state transition with venue sequence/timestamp;
* public trades: every print on the market, price, qty, aggressor if exposed;
* book transitions: every authenticated WS ladder update, full depth, with
  receipt time and book age -- in particular every transition that moves the
  touch TO or THROUGH our resting price;
* our queue, where observable: displayed quantity at our exact price at
  submission and at each subsequent transition;
* fill: time, price, quantity, partial sequence, and **role as the venue reports
  it** (never inferred from our own intent);
* actual commission per leg;
* cancel attempts and their proven results; REST final status;
* settlement: market state, settlementPx, position, and confirmed balance delta.

A qualifying observation requires the book to have reached or crossed our resting
price while the order was live, with the book/trade/private-order sequence
complete enough to order the events. An incomplete sequence is recorded as
`UNRESOLVED` and does not count toward the success condition.

## How each lifecycle is compared against the simulator

For every captured lifecycle, replay the exact window from the authenticated tape
and ask, **using only information available to the simulator at that instant**:

| question | compared against |
|---|---|
| fill or no fill? | venue terminal state |
| at what price? | venue executed price |
| maker or taker? | venue-reported role and commission |
| when, to the nearest book event? | venue fill timestamp |
| queue position assumed | displayed quantity actually observed at our price |

Reported as true positives, true negatives (real non-fills and cancels the
simulator must NOT fill), maker/taker classification accuracy, **false-fill
rate**, and `UNRESOLVED` count. Where the tape cannot establish queue ordering the
simulator must return UNRESOLVED rather than invent a fill -- conservative by
construction, and the `ahead0 = 0`-from-absence rule is removed, since that rule
is what manufactured V3's advantage.

## Explicitly out of scope

- No V1/V2/V3 as profit-seeking strategies.
- No clip or residual-cap change.
- No 1715z.
- No new OOS tape consumed.
- No economic claim from this campaign.

## Status

Preflight only. Not armed, not scheduled, no authorization requested. The
existing triple-key arming (`PMUS_Q1_LIVE`, `PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM`,
plus a campaign-specific key) remains required and unset.

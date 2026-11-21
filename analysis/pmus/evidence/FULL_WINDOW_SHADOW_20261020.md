# First full BTC15m windows with authenticated-book decisions

Deployment `57a892ff`, running commit `bd8ef135`, SUCCESS 2026-10-20T02:39:53Z.
Shadow lock engaged (`L5_LIVE_SHADOW_LOCK {"locked":true,"blocked":["submit"]}`),
authorization EXPIRED, limits unchanged ($5/market, $5 total, 1 market, 1 live
order, $5 daily realized-loss halt, `max_cash_deployed_usd 4.65`,
`cash_policy NO_RECYCLING`). No order was submitted and none could be.

## The blocker is gone

| window | TWO_SIDED_PROBE refusals | reached the arm gate |
|---|---|---|
| 0215z (pre-fix, commit `51b550ff`) | `BOOK_STALE` x21, `PAST_ENTRY_CUTOFF` x38, `TOO_LATE_TO_WITHDRAW` x2 | 0 |
| 0230z (boot at T+9:52) | `PAST_ENTRY_CUTOFF` x12 | 0 |
| **0245z** | **none** | **30** |
| **0300z** | **none** | **22** |

`BOOK_STALE` has not occurred once since the fix. `translation.ok` now reads
`true` where every prior decision read `STRICT_NOT_EVALUABLE`, against books
2-96 ms old (`book_source: authenticated_ws`).

## Eligibility

52 evaluated decisions across the two complete windows, **zero refusals**: the
pair cleared the fee floor, the pair cap, the entry window, the cash envelope,
the deficit gate and the residual cap on every one. The engine then proposed a
real order, which the shadow lock refused at the wire (`ORDER:NOT_ARMED`).

That the intent is the probe's and not the L5-following path is visible in the
record itself: `intent` carries `UP 0.4400 x 3.22 MAKER` while `translation`
in the SAME record reads `0.4700 x 0.22 TAKER`, and the intent is identical
across ticks whose books differ -- the frozen plan.

## The proposed pair (0300z)

Reconstructed with the real book from the record (bid 0.4600 / ask 0.4700,
bid_depth 95.00, ask_depth 2757.14, age 0.008 s) through the deployed
`build_orders`. `depth_ticks=2` reproduces the production intent price and
quantity EXACTLY (0.4400, 3.22), which is what identifies the configuration.

```
UP    0.4400 x 3.22      (rests as a BID at wire 0.44)
DOWN  0.5100 x 3.22      (rests as an ASK at wire 0.49)
pair_cost          0.9500      edge_per_contract  0.0500
gross cash out     $3.039      cash gate charge   $4.6368  (vs $4.65 envelope)
```

Per contract, from `fee_floor`:

| | per contract | pair of 3.22 |
|---|---|---|
| post-only both legs, **no rebate assumed** | +0.056204 | **+$0.181** |
| if BOTH legs somehow paid taker | +0.015507 | +$0.041 |
| maker rebate upside, reported never assumed | +0.006204 | +$0.02 |

`both_positive_if_taker: true`. The pair pays the same on either settlement
outcome, which is the whole point of the shape. The size is bound by the cash
envelope, not by the book: $4.6368 of a $4.65 gate.

Worst case if one leg fills alone and the market settles against it:
**-$1.7022**. That bound, not the +$0.181, is what a live test risks.

## What shadow CANNOT show, and why

The submission claim is taken only inside `if getattr(self.transport,
"armed", False)` -- deliberately, so a shadow decision cannot burn the owner's
one authorization. Under the shadow lock nothing is claimed, so
`probe_legs_claimed` stays empty, `probe_round` stays at round 0, and every
tick re-proposes the SAME first (cheaper) leg.

So continuous shadow validation demonstrates eligibility, pair pricing,
sizing and every gate. It cannot demonstrate the complement being placed, a
second round, a replacement attempt, inventory accounting or settlement
economics, because none of that state advances without a fill. Those paths
have test coverage; they have no production evidence and cannot get any from
shadow. That is a property of the safety design, not a defect.

Three-scenario fill modelling for these legs is likewise not computable from
the records as emitted: it needs the ladder AT each leg price, and the record
carried only the touch. `ts_queue_view` (log-only, additive) adds the
same-rung queue, the level depth and the corrected `cross_price_bracket` for
BOTH legs, including the complement that is never submitted.

## Labels

MODELLED and PROPOSED. Nothing here was submitted, filled, settled or
realized. The +$0.181 is a pair economics calculation on displayed prices,
not a fill, and the rebate is reported separately and assumed nowhere.

---

# 0330z, fully observed (deployment `a0391641`, commit `abb2c3b0`)

32 evaluated decisions, **zero refusals**, 259 `arm_refused_shadow_only_lock`.
The record now carries the whole pair and the ladder at both leg prices.

## The proposed pair, from the record itself

```
UP    0.4000 x 3.32   touch_at_placement 0.4200   alone_cost $1.3880
DOWN  0.5400 x 3.32   touch_at_placement 0.5600   alone_cost $1.8528
pair_cost 0.9400   edge_per_contract 0.0600   depth_ticks 2
gross_cash_usd $3.1008    cash_gate_obligation_usd $4.6480  (gate $4.65)
accumulation_round 0 of 8   leg_to_place UP   legs_claimed {UP:false, DOWN:false}
replaced_l5_intent  UP 0.9 x 1.35 MAKER
```

| | per contract | pair of 3.32 |
|---|---|---|
| post-only both legs, **no rebate assumed** | +0.066105 | **+$0.2192** |
| if BOTH legs paid taker | +0.026056 | +$0.0792 |
| maker rebate upside, never assumed | +0.006105 | +$0.02 |

`both_positive_if_taker: true`. Worst single-leg loss **-$1.8528**.

## Modelled fills: CONSERVATIVE / BASE / OPTIMISTIC

From `two_sided_queue`, read at 03:35:26Z when the book was **0.9100 / 0.9200**
while the plan was frozen against a 0.42 / 0.56 touch:

| leg | wire | rests | same_rung_fifo | cross_price_depth | price must reach us |
|---|---|---|---|---|---|
| UP | 0.4000 | BID | 0 | 22,025.95 | **yes** |
| DOWN | 0.4600 | ASK | 0 | 0 | no |

* **CONSERVATIVE** -- no fill. `conservative_required_volume` is only 3.32,
  but `requires_price_to_reach_us: true`: the bid must fall 51 cents to 0.40
  first, and price distance is cleared by withdrawal, which trade volume
  cannot measure. No fill may be claimed.
* **BASE** -- no fill, same reason. The queue is empty; the price is not there.
* **OPTIMISTIC** -- no fill. This needs a print strictly THROUGH 0.40 and BTC
  went the other way, to 0.91.

**All three scenarios: zero fills, zero residual, $0.00 net settlement.**

## What this window actually teaches

The engine proposed a correctly-priced, positive-expectancy pair and the
market then left it behind. Two consequences, neither visible before today:

1. **The frozen plan does not reprice.** Freezing was the right fix for a
   real defect (re-planning each tick produced legs of DIFFERENT sizes --
   1.89 against 0.98 -- which is not a matched pair). But a plan frozen at a
   0.42 touch is still being proposed at a 0.91 touch, 32 times. In live it
   would have rested the UP leg at 0.40, watched BTC run to 0.91, and
   cancelled it unfilled at the terminal deadline: no position, no loss, a
   spent window. The missing rule is not "reprice the resting leg" (that is
   chasing) -- it is **abandon a round whose plan the market has left, and
   open a new round at the new touch**, which the round machinery already
   supports (8 rounds authorized, round 0 used).
2. **The complement's price is only valid near where it was frozen.** The
   DOWN leg's wire price of 0.46 is now 45 cents BELOW the bid. Submitted
   post-only it would be rejected for crossing, so it never rests. A
   complement must be re-derived from the book at the moment leg one fills,
   not carried from the moment the pair was planned.

That is the useful output of a full window of shadow: the pair economics are
sound and the *sequencing* has a gap, found for the price of a log line
instead of an authorization and an owner handshake.

## Three complete windows

| window | decisions | refusals | plan | outcome |
|---|---|---|---|---|
| 0245z | 30 | 0 | eligible | blocked at the lock |
| 0300z | 22 | 0 | UP 0.4400 / DOWN 0.5100, pair 0.9500, +$0.181 | blocked at the lock |
| 0330z | 32 | 0 | UP 0.4000 / DOWN 0.5400, pair 0.9400, +$0.2192 | blocked at the lock; 0 fills in all 3 scenarios |

84 eligible pair proposals, zero refusals, zero submissions, $0 at risk.

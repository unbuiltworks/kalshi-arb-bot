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

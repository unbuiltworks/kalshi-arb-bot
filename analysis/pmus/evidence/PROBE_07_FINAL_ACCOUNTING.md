# Probe 07 — maker execution, final accounting

Market `cpc-btc-updown-15m-2026-09-25-1945z`, session
`l5probe-20260925-btc15m-07`, account `d51b44a3fa83`, preflight PASSED.
Deployment `51858e0c-62e7-4e33-9edf-b98fff23e09e`, commit `7ad613c`.
`maker_treatment: true`, `maker_only: true`.

**The first probe in this programme that could not cross.** Probes 04-06
executed 25 orders and every one was a taker.

## Funnel

    maker intents        (frozen rule called MAKER, refused unevaluable)
    submitted       9
    accepted        9
    rested          9      every one rested; none crossed
    partial fills   0
    full fills      9
    cancellations   0      nothing was still resting at close-out
    maker fills     9
    taker fills     0

Refusals: **MAKER_ONLY_NO_TAKER 35** (the taker actions, correctly declined),
SUBMIT_OUTCOME_UNKNOWN 1, ORDER_STATE_UNCERTAIN 9. Identity: 54 distinct
actions, 8 distinct refused MARKET_IDENTITY_MISMATCH, **0 foreign admitted**.

## The nine orders

| order | intent | outcome | px | qty | avgPx | commission |
|---|---|---|---:|---:|---:|---:|
| CQBEBQJZEYB5 | BUY_SHORT | NO | 0.75 | 0.29 | 0.7500 | 0.0000 |
| CQBEPBYTMYC6 | BUY_LONG | YES | 0.71 | 1.71 | 0.7100 | 0.0000 |
| CQBENSJMGYBQ | BUY_LONG | YES | 0.71 | 0.54 | 0.7100 | 0.0000 |
| CQBENSV4CYBQ | BUY_SHORT | NO | 0.77 | 2.25 | 0.7700 | 0.0000 |
| CQBFDTWFPYBE | BUY_SHORT | NO | 0.67 | 0.22 | 0.6700 | 0.0000 |
| CQBFGRWHAYBQ | BUY_SHORT | NO | 0.69 | 0.05 | 0.6900 | 0.0000 |
| CQBFN89G8YB5 | BUY_SHORT | NO | 0.67 | 0.22 | 0.6700 | 0.0000 |
| CQBFN89JEYB5 | BUY_LONG | YES | 0.63 | 0.22 | 0.6300 | 0.0000 |
| **CQBG0G16WYC6** | BUY_SHORT | NO | 0.66 | 2.25 | 0.6600 | **-0.0100** |

Every fill was at exactly the limit price -- which is what resting means.
`lastTransactTime` minus `insertTime` gives real rest durations: 35.2s, 22.0s,
21.8s, **204.8s**, 50.4s, 3.2s, 1.5s, 7.4s, 6.3s.

## MAKER REBATE, for the first time

`CQBG0G16WYC6` paid **-0.0100**: a NEGATIVE commission. Eight others paid
exactly zero and none paid anything. Across Probes 04-06, 25 taker orders
paid 0.0000-0.0300 every time.

    Probes 04-06   25 orders, all taker, commission +0.19 total
    Probe 07        9 orders, all maker, commission -0.01 total

That is the mechanism this programme exists to reach, observed on the account.

## Cash identity — closes exactly

    execution     +2.028200     (long -2.0480, short +4.0762)
    commission    +0.010000     a CREDIT, not a cost
    settlement    -2.810000     settlementPx 1.0000; 2.47 long paid,
                                5.28 short owed
    ---------------------------
    net           -0.771800

    balance       63.02925 -> 62.25745    delta -0.77180

**REALIZED NET P&L: -$0.77180.**

## Inventory

Gross **long 2.47 / short 5.28**; **matched 2.47 pairs**, **terminal residual
2.81 short**. The venue reported net `DOWN 2.8100` (5.28 - 2.47) and
reconciliation agreed by identity, so the matched pair was preserved rather
than erased.

The loss is the residual, not the matched book: the pair settles at $1
whatever happens, and 2.81 shares of short YES against a market that settled
at 1.0000 is the whole of it. Same shape as L5's own Sep 23-25 decomposition,
where matched earned +520.7/+411.1/+382.7 bp and residual lost
-719.8/-1,175.5/-824.9.

## Risk

**Peak `risk_exposure_usd` 1.3800**, after CQBENSJMGYBQ, against a $5 cap.
Never close. Peak simultaneous orders is bounded above by 8 and was not the
constraint. Committed_usd read 5.5004 -- above $5 -- which is the discredited
notional metric and gates nothing.

## What went wrong, and is now fixed

1. **Off-tick price.** `0.7399999979` sent, venue echoed `0.73`, exact-echo
   gate refused. Maker prices are now floored to the venue tick.
2. **A pre-POST refusal poisoned the session**, costing nine actions to
   ORDER_STATE_UNCERTAIN although `order_post_attempted: false` proves no
   order existed. Now terminal-known-no-order.
3. **The venue nets the outcome tokens.** Overwriting gross with net would
   delete the matched pair. Now reconciled by identity.

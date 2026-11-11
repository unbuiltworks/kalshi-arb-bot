# The first hypothetical fill, and how it happened

`GEMI-BTC05M2609191605-UP`, BTC5m lane, 2026-09-19T16:02:57Z, TTE 121.0s.

**HYPOTHETICAL. No order existed, nothing was submitted, filled, settled or
realized, and the 0.23 below is not a cost basis.** This is what the frozen
policy's rung would have caught had it been resting.

```
WINDOW GEMI-BTC05M2609191605-UP tte=274.5 cycles=25 -> COMPLETED
  | HYP rested=24 fills=1 first=1 complete=0 | matched=0 residual=0.01
```

## It is the first half of the sequence, and only the first half

`OPEN fill → inventory change` happened, exactly as designed:

- cycle 16 filled a **YES 0.01 @ 0.23**, `was_first_fill: true`;
- inventory moved to **residual +0.01 YES**;
- the policy switched the same instant to `EXTEND YES` + `COMPLETE NO @ 0.65`;
- `GEMINI_RECYCLE` vetoed the EXTEND, leaving exactly one live rung — the
  COMPLETE.

`COMPLETE fill → matched inventory` did **not** happen. `matched` stayed 0,
the residual carried to expiry, and **this is not the trigger.**

## The fill came from being run over

The policy joins the YES bid. It was requoted down with the bid every cycle:

```
0.56 0.51 0.47 0.42 0.42 0.42 0.42 0.42 0.54 0.45 0.41 0.34 0.29 0.26 0.23
```

At cycle 15 the rung sat at **YES 0.23 with 10.0 contracts ahead of it**, and
the ladder read:

```
cycle 15   bids:  0.23 x 10.0   |  0.17 x 882.0  |  0.01 x 10.0
cycle 16   bids:  0.02 x 7500.0                  |  0.01 x 10.0
```

In one ~9-second interval **892 contracts of bid depth disappeared** — the
whole 0.23 level and the 882-lot behind it at 0.17. Our rung was inside that
sweep: the queue ahead was cleared and a 10-contract aggressor SELL reached
us on the way down. `yes_bid` went **0.23 → 0.02** across the same interval.

So the one fill this lane has produced is not patient flow arriving at a
resting quote. **It is adverse selection**: the rung filled precisely because
the price was collapsing through its level, and the best bid was 0.02 the
moment after we held YES at 0.23.

## The complement then ran away, and the policy chased it above par

With residual +0.01 YES, the policy wanted the NO complement at the NO bid.
YES kept falling, so the NO bid kept rising, and the rung was requoted every
single cycle — **eight requotes in eight cycles**, each one to the back of a
new level:

| cycle | TTE | COMPLETE NO | implied pair with YES 0.23 |
|---|---|---|---|
| 16 | 121.0 | 0.65 | 0.88 |
| 17 | 111.1 | 0.65 | 0.88 |
| 18 | 101.3 | 0.76 | 0.99 |
| 19 | 90.9 | 0.78 | **1.01** |
| 20 | 80.9 | 0.79 | **1.02** |
| 21 | 70.8 | 0.81 | **1.04** |
| 22 | 60.5 | 0.82 | **1.05** |
| 23 | 51.2 | 0.84 | **1.07** |

By cycle 19 the pair the policy was working had passed par. It kept chasing.
At cycle 23 the EXTEND dropped out (TTE < `NO_NEW_OPEN_EXTEND_TTE_S` = 60),
at cycle 24 the surface emptied (TTE < `WITHDRAW_ALL_TTE_S` = 30), and the
residual carried to settlement unmatched.

**No pair-cost ceiling was applied and none is proposed here** — the owner
ruled against one, and this table is an observation of what the frozen policy
did, not an argument for a gate.

## What this is evidence of

It reproduces prospectively, in a running lane, the mechanism the failure
decomposition already found retrospectively: *the side a resting maker
accumulates is the side that is becoming wrong.* One occurrence is an
anecdote — the value here is that the mechanism is now **observable live**,
which it was not while the lane reported a hard-coded zero.

## A second first fill, on the other side

`GEMI-BTC05M2609191610-UP` produced a second hypothetical first fill, this
one a **NO** leg: residual **-0.01**. Again no complement filled and again
`matched` stayed 0.

Two first fills on opposite sides, neither recycled, is consistent with a
policy that gets its first leg from whichever side the market is running
through — and then cannot buy the other one back.

Standing count across five BTC5m ledger windows: **2 hypothetical first
fills, 0 complement fills, 0 matched pairs, 96 rested rungs.**

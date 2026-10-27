# Maker rebates: read-only reconciliation attempt

**Result: NOT RECONCILED. `rebate_credited_usd: 0` means "never read", not
"the venue paid nothing".** No estimated rebate is counted as realized
profit anywhere below.

## What the engine reports, and what it means

Both campaigns' `L5_CAMPAIGN_ROLLING` carry:

| field | campaign 1 | campaign 2 |
|---|---:|---:|
| `rebate_estimate_usd` | 0.7311 | 0.1477 |
| `rebate_credited_usd` | **0** | **0** |

The zero is not a measurement. `src/l5_pmus_live_runner.py:2928-2932`:

```
# ONLY ACTUALLY CREDITED rebates, and we hold none: no venue
rebate_credits=(),
rebate_eligibility="UNVERIFIED_NO_VENUE_CREDIT_RECORD"
```

The engine passes an empty credit list by construction. `l5_episode_ledger`
is explicit about the distinction — *"'we have not looked' and 'the venue
paid nothing' are different facts and this table refuses to merge them"* —
and returns `credited_usd: None`, not 0, when no evidence was collected.

## The estimate does not reconcile either, so do not lean on it

Published schedule, verified against all 99 price rows in
`tests/test_pmus_fees.py`: `rebate = theta * C * p * (1-p)` with
`maker_rebate_theta = 0.0125`, banker's rounding to the cent.

| campaign | contracts filled | formula estimate at p≈0.5 | engine reported |
|---|---:|---:|---:|
| 1 (7 settled windows) | 35.54 | ~$0.111 | **0.7311** |
| 2 (8 settled windows) | 42.38 | ~$0.132 | 0.1477 |

Campaign 2's figure reconciles to the formula. **Campaign 1's 0.7311 is
6.6× what its own fill count implies and does not reconcile**, most likely
a counter spanning more than the nonce's windows. I am not using it.

A formula-consistent total across both campaigns is roughly **$0.21 on
77.92 contracts**, about 6.4% of the $3.3376 settled net. Additive, small,
and still unconfirmed.

## Why it cannot be reconciled from this session

The authoritative read is `pmus_incentive_audit.credited_on_fills`, which
sums `commissionNotional*` with the venue's own sign from the
`passiveExecution` / `aggressorExecution` block of each trade row in the
account's own activity walk — negative commission is money credited to us.
It needs **authenticated account credentials**, and this container holds
none: `Q1.has_credentials()` is false here, and the agent proxy injects
only `backboard.railway.com`. The public gateway answers market data and
will not answer for an account's fills.

So this is where it stops, and the next step is named precisely rather than
guessed.

## One thing worth knowing before anyone runs it

`pmus_incentive_audit.REBATE_FLOOR_SHARES = 1.62` — "≥ this credits ≥ $0.01
at p=0.45". Banker's rounding to the cent means a fill below that floor
credits **zero**, whatever the formula says. Campaign 2's fills were 1.74,
1.82, 1.96 and 2.27 shares, all above it; campaign 1 ran smaller clips in
places. So the floor is close to our clip size, and a rebate estimate built
from the continuous formula will overstate what rounding actually pays at
our size. That is a second reason the $0.21 is an upper bound.

## What would settle it

One read-only run of the account activity walk through
`pmus_incentive_audit.credited_on_fills`, from a process that holds the
credentials, over the 15 settled campaign windows. It moves no money,
places no order and touches no limit. Until then the correct label for the
rebate line stays **UNVERIFIED**, and settled net stays $3.3376 with no
rebate inside it.

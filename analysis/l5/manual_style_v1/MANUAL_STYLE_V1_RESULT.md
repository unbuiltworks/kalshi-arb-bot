# MANUAL_STYLE_FAVORITE_ACCUMULATOR_V1 — no gross edge at all, and the seed is not the problem

Shadow/replay only, 28 untouched tape windows, canonical fees, $1
normalization with the manual experiment's own 5:1 seed ratio ($5 UP + $5
DOWN seed, $1 per minute). Rule frozen in `PREREGISTRATION_V1.md` before any
number existed. D0–D4 are closed and untouched. No live orders, no scaling.

## Harness validation passed

The preregistration said **V1-no-seed is rule-identical to Variant A** and
must reproduce its published figure or the lane is void.

| | net per window |
|---|---|
| Variant A (published) | **−1.17787** |
| V1-no-seed (this run) | **−1.17787** |

Exact to five decimals. The harness is sound, and the seed's contribution is
therefore the only new quantity V1 measures.

## Headline: the fee ratio you asked for

| | gross before fees | fees | **gross ÷ fees** |
|---|---|---|---|
| **frozen D0** | **+$3.8959** | $4.0741 | **+0.956** |
| **V1** | **−$19.1548** | $14.4643 | **−1.324** |
| **V1-no-seed** | **−$27.0638** | $5.9165 | **−4.574** |

**Both V1 variants have negative gross.** D0's 0.956 says "the trading
almost pays for its own execution". V1's −1.32 says there is nothing for
execution to eat: the purchases lose money *before* a cent of fee is
charged. **Fees are not this strategy's problem and no execution change
could rescue it.**

## Full results, $1 normalized

| | **V1** (seed + minute accumulation) | **V1-no-seed** (minute accumulation only) |
|---|---|---|
| allocations (seed legs / extensions) | 414 (54 / 360) | 387 (0 / 387) |
| leadership flips | 45 | 45 |
| gross before fees | −19.1548 | −27.0638 |
| gross before fees, per window | −0.6841 | −0.9666 |
| fees | −14.4643 | −5.9165 |
| **gross ÷ fees** | **−1.324** | **−4.574** |
| settlement payout | 610.518 | 359.608 |
| **net** | **−33.6187** | **−32.9804** |
| **mean net/window** | **−1.20067** | **−1.17787** |
| **median net/window** | **−0.78105** | **+0.98680** |
| positive windows | 10/28 | 16/28 |
| worst window | **−9.8270** | −10.9324 |
| leave-best-1 /window | −1.43970 | −1.37635 |
| leave-best-3 /window | −1.83848 | −1.78423 |
| capital required (peak, worst window) | **23.66** | 14.35 |
| max directional residual (shares) | **32.8** | 17.6 |
| capital turnover | 1.00 | 1.00 |

**Gate: failed on every criterion, for both variants.** No scaling study,
no $200 envelope.

## Seed economics vs extension economics, separated

Taken as a standalone book over the 28 windows:

| | cost | fees | settlement payout | **net** | per window |
|---|---|---|---|---|---|
| **the seed alone** | $270.00 | $9.29 | $275.86 | **−$3.43** | −$0.122 |
| **the minute extensions** | $359.67 | $5.18 | $334.66 | **−$30.19** | −$1.078 |
| V1 total | $629.67 | $14.46 | $610.52 | −$33.62 | −$1.201 |

(−3.43 + −30.19 = −33.62, exactly.)

**The seed is not the friction. It is close to a wash.** It buys 258.92
matched shares — guaranteed $1.00 each at settlement — plus an unmatched
remainder, and loses roughly its own fees and half-spreads: **−$0.122 per
window on $9.64 of capital per window.**

**The minute-by-minute favorite accumulation is where the money goes**:
−$30.19 on $359.67 of purchases, a **−8.4% loss on everything it buys.**

## Is the seed useful insurance?

Measured, it is insurance that works and is not worth its price:

| | V1 | V1-no-seed |
|---|---|---|
| worst window | **−9.83** | −10.93 |
| median window | −0.78 | **+0.99** |
| positive windows | 10/28 | **16/28** |
| mean | −1.201 | −1.178 |
| capital | 23.66 | **14.35** |

The seed **does** cut the worst window (by $1.10) and it **does** symmetrize
the distribution — but it pays for that by turning a **positive median
(+$0.99) into a negative one (−$0.78)**, halving the positive-window count,
and requiring 65% more capital, while leaving the mean essentially
unchanged. It converts a right-skewed distribution with a positive median
into a roughly symmetric one with a worse centre.

So: **useful insurance in the tail, guaranteed friction in the body, and a
mean that does not improve.** On this sample I would not pay for it.

## Why V1 loses where D0 earns

D0 and V1 buy the same instrument with the same taker execution. V1 removes
exactly two things, and they are the two things that carried D0's gross:

1. **the strengthening gate** — D0 extends only while the favorite is
   actually strengthening; V1 buys every minute regardless;
2. **the reversal exit** — D0 flattens the directional residual on the
   trail; V1 holds everything to settlement.

Without them, buying the favored outcome at its own market price and holding
is a **fair bet minus the half-spread**, repeated 387 times. That is
precisely what −8.4% on purchases looks like, and it is why the gross sign
flips from +$3.90 (D0) to −$27.06 (V1-no-seed) on the identical venue, the
identical windows and the identical fee schedule.

The manual experiment's shape is therefore **not** the source of the edge
this programme has found. D0's gate-and-exit logic is.

## Scope

This V1 study is a separate preregistered lane. It does not touch the frozen
D0–D4 results, and it is not the reconstruction of the user's actual traded
window — that remains `MANUAL_FAVORITE_DCA`, still blocked on account
credentials, and nothing here substitutes for it. Everything above is
`historical replay`; verified compensation **$0.0000**.

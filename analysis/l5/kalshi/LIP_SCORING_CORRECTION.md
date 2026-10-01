# Correction to 98f4c955: the claimed global q1 ceiling was not a valid bound

Date: 2026-10-01. Supersedes the bound in `CANDIDATE_RANKING.json` and the
"$1.00 per 2.33 days" headline in `LIVE_PROBE_GATE_VERDICT.md`.

## What I claimed

> Across all 8,142 active programs ... the supply ceiling `pool / target_size`
> is maximised globally at **$1.00 per program period**.

## Why it is wrong

The bound assumed a q1 order can earn at most `pool / target_size_fp`, i.e.
that supplying 1 of 1,000 target contracts earns at most a thousandth of the
pool. Kalshi's published scoring does not work that way.

Source: [Liquidity Incentive Program](https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program).

| Rule | Published text |
|---|---|
| Snapshot | "once per second, at a random moment within each second" |
| Reference Price | "Walking down from the best bid, it is the first price level at which cumulative resting size reaches **one fifth of the Target Size**" |
| Multiplier | 1.0x at or better than the Reference Price; otherwise "Discount Factor raised to the number of ticks away" |
| Raw score | "Order Size x Distance Multiplier" |
| Normalization | "each order is divided by the total raw score of all qualifying orders **on that side**" |
| Sides | "Both sides count separately. Your snapshot score is your share of the yes side plus your share of the no side, so a single snapshot is worth at most 2.0" |
| Exclusion | market not open, or "resting orders must meet the Target Size on both the yes side and the no side" |
| Payout | "Your Time Period Score x Time Period Reward x (non-excluded snapshots / total snapshots), rounded down to nearest cent" |
| Minimum | "$1.00" — below that, nothing is paid |

Two features break the bound:

1. **The denominator is a score, not the target size.** Payout is pro-rata
   against *total raw score on that side*, which has no floor at Target Size.
2. **The Reference Price sits only Target/5 deep, and everything at or above
   it scores a flat 1.0.** So the smallest the denominator can structurally be
   is about `Target/5` = 200, not 1,000.

The correct structural ceiling for one contract is therefore

    pool / (Target/5 + 1)

which for the largest pool ($1,000, Target 1,000) is **$4.98 per period**, not
$1.00. **My published ceiling understated the true one by about 5x.** The
earlier verdict reached the right answer through an invalid argument, which is
not the same as being right.

## Recomputed against the documented formula

`scripts/kalshi_lip_scoring.py` walks each real order book, locates the
Reference Price by the Target/5 rule, scores every level by
`size x DiscountFactor^ticks`, and takes the better of the two sides.
Top 150 active programs by pool, books fetched 2026-10-01.

The `TimePeriodScore` normalization is **UNKNOWN** from the public article. If
the pool is conserved it must be the mean snapshot score halved (a participant
owning both sides entirely scores 1.0). Both readings are reported:
*optimistic* = undivided, *conservative* = halved. Both still assume 100%
uptime over the whole period and zero excluded snapshots, so both remain
upper bounds.

| Market | Side | Ref px | Side score | Period | Optimistic | Conservative |
|---|---|---|---|---|---|---|
| KXTRUMPAPPROVE-26OCT02-E38.5 | NO | 0.93 | 410.8 | 24h | $1.4570 | $0.7285 |
| KXTRUMPAPPROVE-26OCT02-E38.8 | NO | 0.88 | 491.4 | 24h | $1.2185 | $0.6092 |
| KXBWAYATTENDANCE-27MAY23B-14500000 | NO | 0.67 | 424.3 | 336h | $1.1756 | $0.5878 |
| KXTRUMPAPPROVE-26OCT02-E38.7 | YES | 0.14 | 527.7 | 24h | $1.1349 | $0.5675 |
| KXBAA-28JANDELIV-640 | YES | 0.54 | 491.0 | 336h | $1.0162 | $0.5081 |

**Clearing the $1.00 minimum payout: 6 of 150 optimistically, 0 of 150
conservatively.**

## The finding that actually decides the gate

The authorized probe is **q1**. Under the documented rules a q1 order cannot
produce $1.00 of compensation anywhere on the venue as it currently stands:
the best conservative figure is $0.73, and **rewards below $1.00 are not
paid at all**. A q1 probe would therefore be credited exactly $0.00 by
construction, regardless of how well it executes.

That makes the probe *uninformative about its own learning objective*. It was
authorized to measure the cost of producing $1 of venue compensation; at q1
the venue's minimum-payout rule floors the compensation at zero, so the
measurement cannot be taken. This is a stronger reason to decline than the one
I gave in 98f4c955, and it does not depend on the invalid bound.

## An implausibility that argues against trusting the point estimates

On KXTRUMPAPPROVE-26OCT02-E38.5 the optimistic credit is **$1.4570 per day for
one NO contract whose entire capital at risk is $0.93**. The credit exceeds
the maximum possible loss on the position. If that were the real rate, the NO
side would not be sitting at a side score of 410 while the YES side of the
same market sits at 2,467 — a ~6x cross-side rate differential that makers
able to switch sides freely would arbitrage away within a day.

I do not have a time series, only a single instant, so I cannot say which
input is wrong. Candidates: the unit hypothesis, the `TimePeriodScore`
normalization, a materially-below-1.0 non-excluded snapshot fraction, or my
reading of the NO-side book. **A model that outputs a risk-free rate of return
is reporting a defect in its inputs, not an opportunity.** The point estimates
above should be treated as diagnostics, not forecasts.

## Component availability

| Component | Status |
|---|---|
| 1. our qualifying raw score | MODELED — 1.0 for q1 at or better than Reference |
| 2. total qualifying raw score, our side | **SOURCE-OBSERVED at one instant**; no time series |
| 3. distance multiplier | SOURCE-OBSERVED (`discount_factor_bps` 5000 -> 0.50) |
| 4. opposite-side Target Size requirement | SOURCE-OBSERVED; met now in all 8 top candidates |
| 5. fraction of non-excluded snapshots | **UNKNOWN** — needs per-second history we do not collect |
| 6. our uptime / fraction of period | assumed 1.0 (generous); genuinely controllable |
| 7. competition through full qualifying depth | SOURCE-OBSERVED to 100 levels, single instant |
| 8. $1.00 minimum payout | SOURCE-OBSERVED — **binding, and decisive** |
| 9. program start/end duration | SOURCE-OBSERVED — 24h and 336h, not uniform |
| unit of `period_reward` | **UNVERIFIED** — see below |
| our eligibility | ACCOUNT-OBSERVED — **unobtainable, no credentials** |

The unit hypothesis (centi-cents) is unchanged and still unverified. If
`period_reward` is in cents rather than centi-cents every dollar figure here
is 100x smaller and nothing clears the minimum by three orders of magnitude.
Only an account-credited reward settles it.

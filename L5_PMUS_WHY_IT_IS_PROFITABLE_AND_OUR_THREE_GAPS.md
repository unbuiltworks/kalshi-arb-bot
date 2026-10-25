# PMUS only. You are right that it is profitable — here is the mechanism and our three gaps.

Kalshi dropped per owner instruction. Everything below is PMUS.

## 1. How L5 actually made money on PMUS

From `src/pmus_incentive_audit.py`, the venue's own records:

> Canonical L5's money was **83% incentives** (maker rebates +$33,929, liquidity rewards
> +$26,956, taker rebates +$7,323 of **+$81,895** total) on **$7.4M of turnover**; its trading
> edge net of residual/timing was **0.18% of turnover**.

As rates of turnover:

| line | L5 | us, measured |
|---|---|---|
| maker rebates | 0.459% | **0.460% — we match him** |
| liquidity rewards | 0.364% ($26,956) | **$0.00 captured, ever** |
| taker rebates | 0.099% | n/a (maker-only) |
| trading edge | **+0.18%** | **-6.458%** |
| combined | **1.287%** | |

$81,895 over the 165-day frozen corpus is **$496/day** on **~$44,848/day of turnover**.
**The $480/day target is L5's actual average daily income.** It is not a trading edge; it is
83% incentive capture at scale.

His trading edge was +0.18% — essentially break-even. **We do not need a brilliant strategy.
We need to stop losing 6.5% and start capturing the rewards.**

## 2. The mechanism, source-confirmed for our account

`analysis/l5/PMUS_SPORTS_INCENTIVES.md`, primary sources fetched 2026-09-10:

> At a pair cost of exactly $1.00 the matched-pair P&L is $0.00 by construction — you pay a
> dollar, you redeem a dollar. So the entire economic content of a $1.00 completion is the
> maker rebate on the two legs.

`rebate = 0.0125 · C · p · (1-p)` per leg, credited **at fill time**, exchange-wide with no
per-category variation, banker's-rounded to the cent:

| entry price | % of the $1.00 tied up | min contracts for 1c on a leg |
|---|---|---|
| 0.50 | **0.625%** | 1.60 |
| 0.75 | 0.469% | 2.13 |
| 0.90 | 0.225% | 4.44 |

A near-$1 completion is worth **0.47%-0.63% of the capital it ties up, risk-free**. The doc
states the formula predicts our own sports credits to the cent — which is the
source-confirmation `CLAUDE.md` requires before unlocking near-$1 pair tolerance.

## 3. Two validated configuration changes

### (a) Completion ceiling 0.96 -> 1.00

Our ceiling refuses near-$1 completion, so we strand instead. Measured, TRAIN non-BTC at $10
notional:

| | ceiling 0.96 | ceiling 1.00 |
|---|---|---|
| stranded windows | 23 | **7** |
| match rate | 60.2% | **86.5%** |
| net | -$46.56 | **-$19.81** |
| return on deployed capital | -8.78% | **-2.77%** |

Strands cut 3x, match rate to 87%, loss cut 2.4x on the larger set. Right direction,
source-confirmed, and exactly the incentive-era L5 behaviour.

### (b) Opening size above the rebate rounding threshold

The rebate is cent-rounded **per print** and the minimum fill for a nonzero credit is
1.60-4.44 contracts by price. At our $0.80 notional every print is ~1.2-1.35 contracts:

**0 of 90 TRAIN prints and 0 of 43 held-out prints earned a single cent. We earn exactly
$0.00 in rebates, by rounding.** At $4.00 notional, 86 of 100 prints pay; at $10, 101 of 101.

These two together take TRAIN from -8.78% to -2.77% return on capital. Necessary, not
sufficient.

## 4. The three remaining gaps, in order of size

1. **STRANDS, worth -6.5% of turnover.** Still 24% of traded windows even at ceiling 1.00.
   Each loses the full premium, roughly 14x what the rebate pays on a completion. This is the
   whole difference between our -6.458% and L5's +0.18%. Not solvable by any entry or
   cancellation rule tested (strands are unpredictable at entry: at n=71 fills the only
   separating feature is the entry price itself, which is tautological), and completion is
   EV-neutral once the market has moved. L5 reached ~32.48 fills/window; we reach 0.25-0.50
   with `max_live_orders = 1`, and continuous two-sided quoting at a 1c crypto spread made it
   worse, not better (-$99 to -$156, t=-4.9..-8.5).
2. **LIQUIDITY REWARDS: 0.364% of turnover for L5, $0.00 for us, ever.** A third of his
   incentive income, and we have never captured a dollar. `rewards_programme_share` is
   0.96-1.00 across all 13 PMUS categories in the 37,116-market universe scan
   (`gamma-api.polymarket.com`), max spread 4.5c (politics 5.5c).
3. **TURNOVER: ~$45,000/day** is what $480/day costs at L5's 1.287%. One market at $20/window
   x 96 windows is $1,920/day, so this needs roughly 20-25 concurrent markets — the
   CONCURRENCY step at the end of the spring order.

## 5. The next step, which costs nothing

Gap 2 is the only one that is pure upside with no new risk: read what the venue has actually
credited us and which PMUS markets carry live reward programmes, pools, target size and
spread. `src/pmus_incentive_audit.py` already does exactly this against
`/v1/incentives/earnings`, `/v1/portfolio/activities` and `/v1/incentives`.

It is **read-only, places no orders and needs no capital**. It cannot run from this container
because outbound network to `polymarket.us` and `gamma-api.polymarket.com` is blocked here
(both return 000). It needs to run where the credentials already are.

Note on the choice of market: we market-make in the tightest-spread PMUS category. Median
spread crypto **0.010** vs politics 0.014, soccer 0.030, tennis 0.17, esports 0.2195,
football 0.33. A maker pair grosses `1 - spread`, so crypto gives +1c/pair against the
0.6-2c/contract of adverse selection we measured. That is why the trading edge is negative
here and why gap 1 may be a market-selection problem rather than a rule problem.

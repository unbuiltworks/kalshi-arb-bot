# Kalshi Step 1 — KXCRYPTOLEAD15M, the currently rewarded series

**Read-only. Zero orders have ever been placed on this venue. Credited rewards: $0.00.**
Everything below is measured from public endpoints or SETTLED-MODELED from public
trades. Nothing here is realized, and no reward is credited or netted.

## The question

> Does the currently rewarded Kalshi series combine L5-quality two-sided flow with
> reward economics capable of overcoming measured fill drag?

## Answer: no — but for a different reason than PMUS

### 1. It is not L5's habitat, structurally

`rules_primary`: *"If Ripple has the highest return out of BTC, ETH, SOL, XRP, HYPE"*.
KXCRYPTOLEAD15M is a **five-way mutually exclusive race** expressed as five binary
legs — not a binary up/down. Settlement confirms it: **1,120 `no` to 280 `yes`**
across 1,400 markets, exactly the 4:1 a 5-way race produces. A two-sided maker on one
leg is not running L5's inventory problem.

### 2. The flow IS balanced — but unreadably thin per market

| | value |
|---|---|
| trades | 2,542 across 120 markets |
| taker side | 1,214 yes / 1,328 no |
| I by count | 0.0448 (floor 0.0158, excess **+0.0290**) |
| **I by volume** | **0.0032** (24,451 / 24,295 contracts) |
| implied r/m floor | **0.006** vs L5 habitat 0.182, PMUS sports 0.919 |
| trades per market | p10 6, p50 **14**, p90 40 |

Pooled, this is the most balanced flow we have ever measured. But at the median of
**14 trades**, a *perfectly balanced* market still measures I = 0.209 — so per-market
imbalance is unreadable here and only the pooled figure carries information.

### 3. Maker drag is not a bleed — it is a fat tail, and that is what kills it

Taking the other side of every real trade at the traded price, settled:

- total **+$2,013.65** on 48,746 contracts = **+$0.0413/contract**, maker-side wins 38.2%

That headline does not survive inspection:

| test | result |
|---|---|
| concentration | top 50 of 2,542 trades = **77.9%** of P&L; top 1 alone = 14.2% |
| **median trade** | **−$0.0100** (mean +$0.0164) |
| markets positive | 72 of 120 (60.0%) |
| **per-market P&L** | p10 **−$42.10**, p50 +$1.65, p90 **+$74.92**, max +$512.13, min **−$344.03** |
| sample validity | 20.0% settled YES — exactly the population rate, so not a selection artifact |

The distribution is median-negative, mean-positive, with 78% of the total in 2% of
the trades. **Per-market swings of ±$300–500 are 15–25× the entire $20 reward pool.**

## Why that settles it

The reward cannot "overcome" this drag because it is not the same order of magnitude
as the variance. $20 per market per 15 minutes is noise next to a per-market P&L
distribution running from −$344 to +$512. Sizing a small bankroll against a
fat-tailed, median-negative distribution to harvest a pool that is a rounding error
on its variance is not a rebate strategy — it is a lottery with a coupon attached.

This is a **different failure from PMUS**. PMUS failed on flow balance (I = 0.315,
structurally unfixable). KXCRYPTOLEAD15M has excellent pooled balance (I = 0.0032)
and fails instead on **thin per-market flow and fat-tailed settlement risk**.

## What is NOT concluded

- Not that Kalshi is dead. The **commodity 15-minute series** (`KXGOLD15M`, `KXWTI15M`,
  `KXSILVER15M`, `KXNATGAS15M`, `KXCOPPER15M`) carry the same $20 pool against a
  **300** target size rather than 1,000, and are genuine binary markets rather than a
  5-way race. They are unmeasured and are the obvious next candidate.
- Not that the +$0.0413 is fake. It is measured on real settled flow. It is simply not
  a quantity a small account can harvest.
- Nothing about credited rewards. **$0.00, and no order has ever been placed here.**

## Reward rules, measured

Five active programs, one per leg: `period_reward` 200000 centi-cents = **$20.00** per
market per 15-minute period, `target_size_fp` 1000, `discount_factor_bps` 5000, rolling
every 15 minutes. Live book at capture: spreads 0.01–0.12, p50 **0.040** → take-both
1.040, rest-both 0.960.

# Preregistration — resting-ladder, replenishment/range, and strike-distance

**Fixed before any result is analyzed.** S3 untouched and frozen at
`8b20a259…87df`. No live orders. No model selected by settlement P&L.

## 0. Identifiability, established first

### What the archive DOES carry
`market_slug, coin, contract_length, token_id, outcome, winner,
is_winning_outcome, market_end_at, captured_at, seconds_to_close,
bid_prices, bid_sizes, ask_prices, ask_sizes, best_bid, best_ask, crossed`
— i.e. the two token books at ~1 s event resolution, plus settlement
outcome.

### What it does NOT carry — UNIDENTIFIABLE
**No strike. No reference price. No settlement index.** Confirmed against
the parquet schema. Therefore:

- the **exact settlement index is UNIDENTIFIABLE** and will be reported as
  such;
- **Coinbase is never silently substituted.** Any external series runs only
  under the label `PROXY_NOT_SETTLEMENT_INDEX`, as in the completed §4
  work, and the exact-strike question stays **UNRESOLVED**.

### One structural opening, to be tested not assumed
A `btc-updown-15m` market plausibly resolves on whether BTC is higher at
window close than at window **open**. If so, `strike = reference price at
window open`, and then

```
distance_from_strike(t) = reference(t) − reference(open)
```

is a **difference**, in which a proxy's level bias largely cancels — far
better conditioned than an absolute strike. This is a hypothesis about
venue mechanics, so it is **gated**:

> **G-STRIKE.** For all 12 windows, does `sign(proxy_close − proxy_open)`
> match the archive's own `winner`? Pass = **≥11/12**.
> On pass: run strike-distance as `return since open`, labelled
> `PROXY_RELATIVE_TO_OWN_OPEN`, with the mechanism stated as inferred.
> On fail: report the strike test as **NOT IDENTIFIABLE** and run no
> strike-distance analysis at all.

Even on pass, the quantity remains a proxy for the index; it is never
called the settlement index, and no absolute strike in dollars is claimed
beyond the proxy's own level.

### Crossed-book exposure
Per `CROSSED_BOOK_DEFECT.md`, the archive's **own `crossed` column** flags
3.08 % of rows. This study **reads that column** and reports exposure from
it. Ladder reconstruction uses **bids only** (resting maker interest on the
side L5 buys), so ask corruption does not enter the ladder results.

## 1. Per-fill reconstruction (BTC15m maker fills, verified L2 overlap)

For every L5 **maker** fill: fill quantity · inferred parent clip where
defensible · cumulative consumption of that clip · fill price · price level
· BBO immediately before · ticks from touch · **first time that exact price
level is observable/relevant before its first L5 fill** · subsequent fills
at the same level · fills at adjacent L5 levels · elapsed time between
partial fills · outcome · L5 inventory at the time · TTE.

Clip inference uses the frozen break rules B1/B2/B3. `≤80` is a **ceiling,
not an assumed order size**.

## 2. Q1 — irregular sizes as partial consumption

Share of apparently irregular fill quantities parsimoniously explained as
partial consumption of larger latent clips. **Fill count and share volume
reported separately**, never pooled.

Parsimony criterion, fixed now: a fill is "explained" if it belongs to an
inferred clip with ≥2 fills whose total lands within **0.75 shares** of a
lattice multiple (80, 160, 240, 320…), *or* whose total is itself ≤80 and
the clip is terminated by an evidenced break. A singleton fill at an
irregular size with no clip context is **NOT** explained and counts
against the hypothesis.

## 3. Q2 — minimum ladder width, a LOWER BOUND

At each second, the **minimum number of distinct price levels per outcome**
required to explain subsequent L5 fills **without assuming cancellations or
reposts the archive cannot evidence.**

Reported **explicitly and only as a lower bound**. The phrase "observed
resting orders" will not be used, because unfilled and cancelled orders are
invisible.

## 4. Q3 — concurrency

Search for `A → B → A` level returns and rapid multi-level bursts that a
simultaneously-resting ladder explains more naturally than reactive
reposting.

**Resolution honesty, fixed now:** block timestamps are whole seconds. Two
fills in the **same second** cannot distinguish concurrency from fast
reposting and are reported as `INDETERMINATE_SAME_SECOND`. A concurrency
claim requires an `A → B → A` return with **no evidenced replenishment at A
in between**, spanning ≥2 distinct seconds.

## 5. Q4 — ladder width, and what 0.06 actually is

Per outcome/window: best inferred level → deepest inferred level, in
**dollars** and in **ticks from contemporaneous touch**.

**Test of the ~0.06 displacement bound.** It was derived as the p99 of
*fill offsets below the touch*. A fill-location statistic and a ladder-width
feature make different predictions:

| | if 0.06 is a WIDTH feature | if it is only a FILL-LOCATION statistic |
| --- | --- | --- |
| deepest inferred level per window | clusters at ≈0.06 with low dispersion | scales with how far the touch travelled |
| correlation with touch range | weak | strong (rho ≥ 0.5) |

*Bar:* WIDTH feature requires deepest-level IQR ≤ 0.02 **and** |rho| with
touch travel < 0.30, in ≥9/12 windows. Otherwise it is reported as a
fill-location statistic.

## 6. REPLENISHMENT / RANGE test

**Primary alternative model:** L5 maintains bounded resting maker liquidity
across a price range and **replenishes depleted latent clips**, rather than
choosing each observed fill quantity/side independently.

For each `(window, outcome, price)` build cumulative L5 maker-filled
quantity through time. **Observed cumulative quantity is never literally
reset**; instead the **minimum number of latent clips** needed to explain
the consumption is inferred.

**R1 — lattice clustering.** Does total consumption at individual price
levels cluster near **80, 160, 240, 320…** more strongly than baselines?
Baselines fixed now: (a) continuous uniform over the observed range,
(b) randomised fills preserving the empirical single-fill size
distribution, (c) the empirical distribution shifted by half a lattice step
(40) as a phase control.
*Bar:* excess mass within ±0.75 of a multiple ≥**3×** every baseline, in
≥9/12 windows.

**R2 — partial-clip variant.** R1 repeated allowing the **empirically
supported partial-clip distribution** rather than assuming every clip
starts at 80. If R1 passes only under a forced-80 assumption, that is
reported as an artifact of the assumption.

**R3 — what happens after a depletion event**, taxonomised as exactly one
of:

| operation | definition |
| --- | --- |
| **REPLENISH** | the consumed level reappears (further fills at the same price) |
| **RECENTER** | the consumed/old level disappears and new exposure appears nearer the moved market |
| **EXTEND LADDER** | an additional deeper level becomes active |
| **WITHDRAW** | no replacement, as TTE/inventory constraints bind |

Latency to the next attributable fill is measured and reported **explicitly
as an upper bound on replacement latency**, because unfilled order
placement is unobservable.

**R4 — do the four operations beat per-fill decisions?** Compared by
description length: operations needed to describe the window vs the number
of fills. *Bar:* operations ≤ **0.5 ×** fills, in ≥9/12 windows.

## 7. Envelope reconstruction and model comparison

Per outcome, per second: lowest filled resting level · highest filled
resting level · width in **cents** · width in **venue ticks** · distance
from contemporaneous touch · center relative to probability · and how the
envelope moves as probability moves.

### Candidate models — and their degeneracies, established before results

On a prediction market **the contract price IS the probability**, so of the
five named candidates only **three** are distinct:

| named candidate | status |
| --- | --- |
| fixed absolute contract-price range | **E1** |
| fixed distance around 0.50 | **nested in E1** (its symmetric special case) |
| fixed distance around current touch | **E2** |
| probability-following / recentering range | **≡ E2** — touch *is* probability; verified identical at p = 0.25, 0.50, 0.80 |
| inventory-dependent asymmetric range | **E3** |

A win by E2 may **not** be reported as two independent confirmations. This
is the fourth such collapse in this program; the check is routine.

### Preregistered comparison metrics

Each model has its parameter(s) fitted on 11 windows and scored on the
held-out 12th (**LOWO**). Score = median L1 error in predicting, per
second, the envelope **center** and **width**, reported separately.

*Bar:* a model wins if its LOWO median L1 is **≥20 % better** than the next
best **and** better in **≥9/12** held-out windows. Otherwise:
**NOT DISCRIMINATED**.

## 8. Strike-distance test (conditional on G-STRIKE)

Per L5 fill: distance from strike in **$** · in **bps of strike** ·
normalized by recent proxy volatility (trailing 15 min) · **direction**
relative to strike · TTE.

Compared across: all fills · maker fills · inferred **first fill of each
latent clip** · partial fills · **EXTEND** · **COMPRESS** · withdrawals.

Two questions:

1. Is L5 activity disproportionately concentrated **near the strike**?
   *Bar:* activity density within ±0.25 proxy-σ of strike ≥**2×** the
   density beyond ±1σ, in ≥9/12 windows.
2. Does inferred **ladder activation** follow BTC distance-from-strike more
   consistently than contract probability?
   *Bar:* |rho| with distance-from-strike exceeds |rho| with contract
   probability by ≥**0.15**, in ≥9/12 windows.

**Confound recorded now:** contract probability is itself a function of
distance-from-strike and TTE, so the two regressors are collinear by
construction. Question 2 is therefore a *relative* consistency test, not a
causal separation, and will be reported with that limit stated.

## 9. Pre-positioned vs reactive

| | **PRE-POSITIONED** | **REACTIVE** |
| --- | --- | --- |
| claim | bounded liquidity rests at multiple levels on both outcomes; market/BTC movement decides which pieces are consumed | each fill causes the next placement |
| level age (time from a level first becoming reachable to its first L5 fill) | wide distribution, including long ages | short and concentrated |
| `A → B → A` returns without evidenced replenishment at A | expected | near-impossible without an invisible cancel |
| deepest-level fills | occur without any preceding fill nearby | require a preceding fill to trigger |

*Bar for PRE-POSITIONED:* ≥**20 %** of levels have first-fill age ≥**30 s**
**and** qualifying `A → B → A` returns occur in ≥**9/12** windows.
*Bar for REACTIVE:* median level age ≤**5 s** **and** <5 % qualifying
returns.
Neither satisfied → **NOT DISCRIMINATED**.

Selection is on these structural metrics only. **Settlement P&L is not used
to choose between models.**

## 10. Required output

How much of what has been called L5 "sizing" is **counterparty-determined
partial filling**, and what evidence exists that L5 maintains a
pre-positioned multi-level two-sided ladder around strike-sensitive
probability movement — with every claim tiered:

**DIRECTLY OBSERVED / STRONGLY INFERRED / POSSIBLE / UNIDENTIFIABLE.**

A null or NOT-DISCRIMINATED result is a complete answer. Recorded in
advance: the exact strike is already **UNIDENTIFIABLE**, and ladder width
can only ever be a **lower bound**.

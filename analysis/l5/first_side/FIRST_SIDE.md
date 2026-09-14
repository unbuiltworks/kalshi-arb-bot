# L5's first side: CHEAPER refuted, DEARER is the best simple rule, no model earns adoption

Sequential, no outcome leakage, chronological out-of-sample. **Nothing here is
implemented.** `cheaper touch` was not replaced with `dearer touch` in any
production or Gemini path.

## Design

**Target.** y = 1 iff L5's first buy in a market is on Up.

**The move that makes it non-circular.** We observe only the price of the side
he *chose*, so "chosen-side price" would encode the answer. In a binary market
the two outcomes price to ~1.00, so the market's **Up** price is recoverable
either way: `up_price = fill_price if he bought Up else 1 − fill_price`. That
is a property of the market, identical under either choice, and every price
feature is built from it. No feature is a function of the label.

**No outcome leakage.** Which side actually won the current window never
enters a feature. Only the *previous* window's resolution is used, and only
where it resolved strictly before the current first fill.

**UNAVAILABLE ≠ ZERO.** The public tape carries L5's fills, not the book. So
`spread`, `depth_at_touch`, `order_flow_imbalance`, `intra_window_trajectory`,
`book_updates` and `queue_position` are **NOT AVAILABLE historically** and are
named as such rather than imputed as zero — a zero would be a measurement
claim we cannot make.

**Class balance.** Up 8,300 (48.69%) / Down 8,746 (51.31%). Majority-class
floor **0.5131**. Split chronologically 60/20/20: train 10,227, valid 3,409,
test 3,410.

## Predictor leaderboard — chronological TEST, like-for-like (n = 3,041)

| rule | accuracy | log loss |
|---|---|---|
| **DEARER / FAVORITE** | **0.6600** | 4.6975 |
| **MODEL** (logistic, 8 features) | 0.6409 | **0.6697** |
| MOMENTUM | 0.5896 | 5.6698 |
| DOWN | 0.5025 | 6.8737 |
| UP | 0.4975 | 6.9418 |
| RANDOM | 0.4972 | 1.0068 |
| MEAN_REVERSION | 0.4104 | 8.1457 |
| **CHEAPER / UNDERDOG** — *our production rule* | **0.3430** | 9.0771 |

**CHEAPER is materially worse than a coin flip** (0.343 against a 0.513
majority floor). Its complement DEARER is the informative statement.

## The smallest model does NOT earn adoption

Test accuracy **0.6409 vs DEARER's 0.6600** — the 8-feature logistic is
*worse* on accuracy while being far better calibrated (log loss 0.6697 vs
4.6975; predicted 0.335→observed 0.345, 0.497→0.521, 0.662→0.625). Its
dominant standardized coefficient is `up_price` at **+0.3632**, with every
other feature an order of magnitude smaller:

| feature | coef |
|---|---|
| up_price | **+0.3632** |
| prev_winner_up | −0.1304 |
| prev_y_up | +0.1041 |
| prev_move | +0.0306 |
| is_eth | −0.0211 |
| dist_from_50 | +0.0176 |
| is_15m | +0.0102 |
| tte_norm | −0.0000 |

**The model rediscovers DEARER and adds nothing.** By the owner's own
criterion — the smallest interpretable model that *materially improves* OOS —
**no model qualifies.** The honest output is a calibrated probability, not a
better rule.

## Cohort stability — DEARER holds in 4 of 4

Test set:

| cohort | n | %Up | CHEAPER | **DEARER** | MODEL |
|---|---|---|---|---|---|
| BTC5m | 1,444 | 0.495 | 0.3636 | **0.6364** | 0.6267 |
| ETH5m | 925 | 0.501 | 0.2865 | **0.7189** | 0.6897 |
| BTC15m | 519 | 0.503 | 0.4220 | **0.5838** | 0.5742 |
| ETH15m | 153 | 0.484 | 0.2222 | **0.7843** | 0.7059 |

Lifetime: BTC5m 0.6483 (n=8,089) · BTC15m **0.5433** (n=5,546) · ETH5m 0.6803
(n=2,831) · ETH15m 0.7138 (n=580).

**It is not a single-cohort artifact** — but it is **weakest precisely in
BTC15m (0.5433)**, the cohort that carries essentially all of L5's trading
profit (+$19,176.71 of +$15,707.45 net). That tension is unresolved and is not
argued away.

Over time (6 chronological blocks): 0.5157 · 0.6396 · 0.6213 · 0.6572 ·
0.6448 · 0.6515. **The tendency is absent in the earliest block and stable
thereafter** — it emerged rather than being constant.

## What is and is not established

* **ESTABLISHED:** `cheaper touch` is refuted out-of-sample, in all four
  cohorts, on 17,046 markets. Our clone has been systematically choosing the
  opposite side from L5.
* **ESTABLISHED:** a dearer-side tendency of ~0.66 OOS, stable across cohorts
  and (after the first block) over time.
* **NOT ESTABLISHED:** any *rule*. 34% of L5's opens are still on the cheaper
  side; a deterministic dearer rule would be wrong a third of the time. The
  true decision almost certainly depends on the book and flow features the
  public tape **cannot supply**.
* **NOT DONE:** no production, Gemini or PMUS path was changed.

---

# P0 follow-up: what separates a favourite-open from an underdog-open?

Target: `y = 1` iff L5's first fill price > 0.50. Base rate **0.6015** (n =
17,046). `dist_from_50` is `|up_price − 0.5|` — symmetric, carries no sign,
so it is not circular with the label. Chronological 60/20/20; buckets
measured on TRAIN and verified on TEST.

## The discriminator is distance from 0.50, and it is an INVERTED U

| \|price − 0.50\| | n train | P(DEAR) train | n test | P(DEAR) test |
|---|---|---|---|---|
| < 0.05 | 3,007 | **0.4566** | 676 | **0.5089** |
| 0.05–0.15 | 3,563 | 0.6149 | 1,001 | **0.7433** |
| 0.15–0.25 | 1,612 | 0.6706 | 694 | **0.7565** |
| 0.25–0.35 | 1,011 | 0.6924 | 515 | 0.6641 |
| ≥ 0.35 | 1,034 | 0.5861 | 524 | **0.4981** |

**DEARER is a mid-band phenomenon, not a rule.** When the market is balanced
(|d| < 0.05) L5 is a **coin flip** — he has no favourite preference at all.
The preference peaks in the 0.05–0.35 band (0.61–0.76) and then **collapses
back to chance at the extremes** (|d| ≥ 0.35 → 0.4981 out-of-sample).

That shape holds in both splits and explains why a flat DEARER rule tops out
near 66%: it is applying a mid-band tendency uniformly, including to the two
regions where L5 shows no preference whatsoever.

## Second discriminator: cohort

| cohort | P(DEAR) train | P(DEAR) test |
|---|---|---|
| **BTC15m** | **0.4999** | 0.5530 |
| BTC5m | 0.6432 | 0.6277 |
| ETH5m | 0.6996 | 0.7038 |

**BTC15m shows essentially NO dear preference (0.4999 in train).** This is the
same cohort where DEARER scored weakest (0.5433 lifetime) and, separately, the
cohort carrying nearly all of L5's trading profit. A single first-side rule
cannot be right across cohorts.

## Weaker signals

Series volatility has a mild monotone effect (vol < 0.05 → 0.575/0.507;
≥ 0.12 → 0.616/0.667). TTE fraction, |previous window move|, previous opening
side and hour-of-day are all flat in the 0.56–0.68 range with no structure
that survives both splits.

## What this means

* **Do not deploy DEARER.** It is a uniform rule fitted over a
  non-uniform behaviour, and it is wrong in the two regions (balanced, and
  extreme) where L5 is at chance.
* The honest conditional statement is
  `P(DEAR | 0.05 ≤ |d| ≤ 0.35, cohort ≠ BTC15m) ≈ 0.65–0.76`, and
  `P(DEAR | |d| < 0.05 or |d| ≥ 0.35) ≈ 0.50`.
* Even that is a *tendency*, not a decision rule. The features that would
  actually separate the two cases — spread, depth, order flow, intra-window
  trajectory — are **UNAVAILABLE** in the public tape and are named as such,
  never imputed.
* **Nothing is deployed and no production path changed.**

---

# P0/P1 follow-up: the calibrated surface, and where prediction stops

## The frozen surface

`P(DEAR | cohort, |price−0.50| bin, TTE bin)`, fitted on TRAIN only, each cell
shrunk toward its cohort rate with a pseudo-count fixed in advance (k = 25).
No monotonicity assumed; no settlement outcome used.

| model | VALID acc | VALID log loss | TEST acc | TEST log loss |
|---|---|---|---|---|
| CHEAPER | 0.3881 | 8.4538 | 0.3501 | 8.9781 |
| DEARER | 0.6119 | 5.3617 | 0.6499 | 4.8375 |
| 50-50 | — * | 0.6931 | — * | 0.6931 |
| COHORT_RATE | 0.5914 | 0.6755 | 0.6337 | 0.6453 |
| **SURFACE** | **0.6169** | **0.6547** | **0.6510** | **0.6321** |

\* a constant 0.5 scores the same *accuracy* as DEARER because `p ≥ 0.5`
always predicts DEAR; only its log loss is meaningful.

**SURFACE wins on both metrics**, and it is the only entry that is both
better than chance and honestly calibrated:

| predicted | n | mean pred | observed |
|---|---|---|---|
| 0.4–0.5 | 458 | 0.4597 | 0.4956 |
| 0.5–0.6 | 704 | 0.5618 | 0.6264 |
| 0.6–0.7 | 850 | 0.6433 | 0.6071 |
| 0.7–0.8 | 1,281 | 0.7310 | 0.7291 |
| 0.8–0.9 | 117 | 0.8305 | 0.8376 |

But the gain is **modest**: 0.6321 against a 0.6931 coin-flip is ~9%. Most of
L5's first-side choice remains unexplained by anything we can observe.

## Per cohort (TEST)

| cohort | n | P(DEAR) | DEARER acc | SURFACE acc | SURFACE ll |
|---|---|---|---|---|---|
| BTC5m | 1,555 | 0.6277 | 0.6277 | 0.6174 | 0.6548 |
| ETH5m | 1,158 | 0.7038 | 0.7038 | 0.7038 | **0.5830** |
| **BTC15m** | 519 | **0.5530** | 0.5530 | **0.5915** | 0.6795 |
| ETH15m | 178 | 0.7753 | 0.7753 | 0.7753 | 0.6158 |

BTC15m is the only cohort where the surface **beats** the flat rule on
accuracy — and also the only one whose log loss barely improves on a coin
flip.

## BTC15m IS a different regime, for two compounding reasons

| \|d\| | BTC5m P(DEAR) train/test | BTC15m P(DEAR) train/test |
|---|---|---|
| < 0.05 | 0.4724 / 0.5404 | 0.4428 / 0.4545 |
| 0.05–0.15 | **0.6878** / 0.7101 | 0.5473 / 0.6441 |
| 0.15–0.25 | **0.7027** / 0.6887 | 0.5347 / 0.7000 |
| 0.25–0.35 | **0.7236** / 0.5616 | 0.5126 / n/a |
| ≥ 0.35 | 0.5956 / 0.5318 | 0.5843 / n/a |

1. **Its conditional preference is weaker everywhere** — BTC5m reaches
   0.69–0.72 in the mid bands; BTC15m never exceeds 0.585 and mostly sits at
   0.51–0.55.
2. **Its mass sits where nobody has a preference.** 3,945 of 4,557 BTC15m
   train markets are inside |d| < 0.15, the region where even BTC5m is at
   chance. A longer horizon prices closer to 0.50.

So BTC15m is not "DEARER failing" — it is a cohort that spends most of its
life in the balanced state where L5 shows **no side preference at all**.

## P1: where price stops explaining, nothing else takes over

Tested in the three collapse regions on temporally valid variables only.
**UNAVAILABLE and therefore not tested: spread, depth, order flow,
intra-window trajectory, book updates, queue position.**

**Balanced (|d| < 0.05)** — base 0.4566 train / 0.5089 test. Previous-window
move: 0.42–0.47 train → 0.48–0.54 test. Volatility: 0.45–0.46 train →
0.42–0.55 test. Neither ordering survives the split. The only stable signal is
cohort (BTC15m 0.4428 → 0.4545, consistently below 0.5).

**Extreme (|d| ≥ 0.35)** — base 0.5861 train → **0.4981 test**. Every bucket
inverts across the split (previous-flat 0.6589 → 0.4828; TTE < 50% 0.5927 →
0.4346). Nothing holds.

**BTC15m** — base 0.4999 train → 0.5530 test; test cells for most buckets are
single digits and cannot support a claim.

**Conclusion: conditional on what we can observe, side choice in these regions
is effectively stochastic.** No non-price variable improves out-of-sample
prediction. The features that would plausibly separate the cases are exactly
the ones the public tape does not carry, and they stay UNKNOWN.

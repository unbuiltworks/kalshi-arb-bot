# RESIDUAL_EXIT_FORENSICS_V1 — how L5 gets out of directional imbalance

`analysis/l5/residual_exit/forensics.py` · BTC15m · 5,546 windows, 693,916
fills, **zero sells**, 2026-03-19 → 08-31 · **38,180 residual episodes**
· machine-readable output in `btc15m_v1.json`

Behavioral research. Nothing here changes the frozen PMUS sweep, proposes a
policy, or moves money.

## Method

BTC15m contains no sells at all, so inventory on each side only ever increases
and residual moves only by buying. That collapses the vocabulary to four
transitions — `OPEN`, `EXTEND`, `COMPLETE` (partial or exactly flat), `SWITCH`
(an opposite buy large enough to cross zero). An **episode** runs from the fill
that takes residual off zero to whichever of three things ends it:
`COMPLETED_FLAT`, `FLIPPED`, or `CARRIED_TO_SETTLEMENT`.

**Reconciliation.** Summed episode residual P&L is **−$144,064.99997**, which
is the same −$144,065 that `analysis/l5/btc15m/decompose.py` reports at window
level. Matched P&L does **not** reconcile and is not expected to: a window's
matched quantity is `min(total_up, total_dn)` over all its fills, an episode's
is the min inside that episode, and partitioning can only shrink the sum
(+$93,183 here vs +$162,257 at window level). The episode figure is the
matched economics *created inside each imbalance*, not the window's.

**TRAIN/OOS is chronological**, never random: 26,726 episodes to 2026-08-15,
11,454 after. A shuffled split would leak a regime that demonstrably changed.

## 1. L5 does not complete to flat. He overshoots.

| exit | n | share | median dur | median \|res\| at open | median max subsequent \|res\| | median episode pair cost |
|---|---|---|---|---|---|---|
| FLIPPED — away from favored | 16,248 | 42.6% | 33 s | 14.1 | 57.7 | 0.9801 |
| FLIPPED — toward favored | 15,274 | 40.0% | 24 s | 12.7 | 48.4 | 0.9856 |
| **CARRIED_TO_SETTLEMENT** | 5,545 | 14.5% | 305 s | 14.2 | 213.5 | 0.9856 |
| FLIPPED — no favored side (p = 0.50) | 661 | 1.7% | 54 s | 16.2 | 58.1 | 0.9901 |
| **COMPLETED_FLAT** | **452** | **1.2%** | 18 s | 25.0 | 30.0 | **0.9950** |

**Exactly one of 5,546 windows ended flat.** The other 5,545 settled holding a
residual whose median size is **141.8 shares — 7.7% of all shares traded in the
window** (p75 269, p90 462, p99 954; the smallest is 0.064 shares, so this is
not a floating-point artifact).

Two things follow immediately.

* **Flat completion is the rarest thing L5 does and the most expensive when he
  does it.** Its median episode pair cost is 0.9950 against 0.980–0.986 for
  flips, its median *attainable* pair at the last prints is **1.01**, and its
  summed matched P&L is **negative (−$42.9)**. Every other exit is cheaper.
* **It was being abandoned, chronologically.** 15.4% of episodes (Mar) → 2.5%
  (Apr) → 2.1% (May) → 1.3% (Jun) → 1.6% (Jul) → **0.32% (Aug)**. Out of
  sample the rate is 0.24% and in most feature bins it is exactly zero.

This is direct evidence for the owner's correction: **"residual → zero" is not
L5's objective and hard-coding it as ours would copy the one behaviour he
priced worst.**

## 2. The next action is driven by his own run, not by price direction

P(next action = `EXTEND`) is **0.66** unconditionally. Only one feature both
separates it and keeps its ordering out of sample:

| feature | train spread | OOS spread | ordering replicates | Spearman |
|---|---|---|---|---|
| **same-side run length** | 0.113 | **0.152** | **yes** | **1.00** |
| \|residual\| at open | 0.089 | 0.143 | no | 0.80 |
| **Δp over 60 s** | 0.044 | 0.050 | **yes** | **1.00** |
| time to expiry | 0.099 | 0.071 | no | −0.60 |
| p_up at last print | 0.061 | 0.038 | no | 0.03 |
| window progress | 0.056 | 0.021 | no | −0.40 |
| attainable pair cost | 0.002 | 0.027 | no | −0.50 |
| **residual is on the favored side** | **0.004** | **0.007** | yes | — |

Run length, OOS: 1 → 0.648, 2 → 0.581, 3 → 0.656, **4+ → 0.733**. He continues
what he is already doing, and more so the longer he has been doing it.

**Momentum is falsified as stated, and what replaces it is volatility.** The
Δp-over-60s relationship replicates perfectly (ρ = 1.00) and is **symmetric**:
P(extend) is highest at *both* extremes — 0.672 for Δp > +0.05 and 0.663 for
Δp < −0.05 — and lowest when price is flat (0.622 at −0.01…+0.01). The
**magnitude** of recent movement predicts extension; the **sign** does not.

\|residual\| is a real brake: 1–5 shares → 0.740 extend, 20–100 → 0.606 (OOS).

## 3. EXPLORATORY_V2 as stated is falsified on L5's own tape

The hypothesis was that once pair economics exceed 1.00, L5 disproportionately
accumulates the **favored** outcome. He does not.

P(his next fill lands on the favored side), ties at exactly 0.50 excluded:

| attainable pair at last prints | TRAIN n | P(favored) | OOS n | P(favored) |
|---|---|---|---|---|
| ≤ 0.96 | 10,483 | 0.4930 | 4,911 | 0.4932 |
| 0.96–0.99 | 501 | 0.5010 | 207 | 0.5507 |
| 1.00–1.02 | 295 | 0.4881 | — | — |
| **> 1.02** | 10,392 | **0.4895** | 4,738 | **0.4707** |
| **base rate** | 21,850 | **0.4919** | 10,050 | **0.4847** |

It is a coin flip that is very slightly *below* half, and expensive pair
economics move it **down**, not up. The one bin above 0.55 (0.96–0.99 OOS) has
n = 207 and its TRAIN value is 0.5010, so it does not replicate.

**The confound, controlled.** Conditioning on where the residual already sat
collapses the apparent effect entirely, and the columns are flat across pair
cost:

| residual at open | pair ≤ 0.96 | pair > 1.02 |
|---|---|---|
| already favored (TRAIN) | 0.6447 | 0.6407 |
| against favored (TRAIN) | 0.3448 | 0.3437 |
| already favored (OOS) | 0.6492 | 0.6266 |
| against favored (OOS) | 0.3408 | 0.3146 |

0.64 / 0.34 is just P(extend) ≈ 0.66 re-expressed. **What looks like favored-side
preference is run persistence.** Pair economics crossing 1.00 changes nothing
about which side he buys.

## 4. Carrying residual to settlement lost money, including on the favored side

| | n | P(residual profitable) | sum residual P&L |
|---|---|---|---|
| TRAIN, residual **favored** at open | 1,878 | 0.801 | **+$1,631** |
| TRAIN, residual **against** favored | 2,361 | 0.137 | −$63,337 |
| **OOS, residual favored at open** | 432 | 0.692 | **−$12,971** |
| **OOS, residual against favored** | 802 | 0.057 | −$66,414 |

The high favored-side win rate is mechanical — "favored" means the market
priced it above 0.5 — and it is *priced*: at a cost basis above 0.5, a 69% win
rate is a loss. **Favored-side residual carried to settlement was barely
positive in training and clearly negative out of sample.** The settlement leg
of the FAVORED_SIDE_MAKER premise fails on L5's own history.

He also carries the *unfavored* residual roughly twice as often (2,361 vs 1,878
TRAIN; 802 vs 432 OOS), which is the signature of passive quoting: you
accumulate the side that is falling.

## 5. Patience paid — and then stopped paying

Cost of the completing leg at the moment the imbalance opened, minus the cost
actually paid. Positive = waiting got a cheaper opposite leg.

| wait to opposite fill | TRAIN n | median | P(improved) | OOS n | median | P(improved) |
|---|---|---|---|---|---|---|
| ≤ 5 s | 6,508 | +0.010 | 0.703 | 4,496 | +0.010 | **0.683** |
| 5–15 s | 6,499 | +0.010 | 0.619 | 3,290 | +0.010 | 0.555 |
| 15–60 s | 9,139 | +0.010 | 0.571 | 2,976 | **0.000** | **0.498** |
| 60–180 s | 3,444 | +0.010 | 0.512 | 510 | **−0.010** | **0.418** |
| > 180 s | 401 | +0.027 | 0.529 | — | — | — |

In training every wait bucket improved the price. **Out of sample the benefit
is gone by 15 seconds and negative past a minute** (median −1 tick, mean
−$0.0149/share, improved only 41.8% of the time).

**This is selection-biased and the bias cannot be removed here.** It conditions
on a completing fill having happened. Episodes where L5 quoted patiently and
was never hit leave no trace — 907 carried episodes never saw an opposite fill
at all, and an unfilled order is invisible in this dataset. So this answers
"given the other side eventually traded, how good was that price", **not** "what
is a waiting policy worth". The second question needs a book that does not exist
here.

## What is NOT observable, named rather than imputed

* Book state at any instant — bid, ask, spread, depth, imbalance.
* Queue position, and therefore whether any quote would have filled.
* **L5's unfilled, resting or cancelled orders.** A quote that did not fill
  leaves no trace, so *"L5 waited"* and *"L5 quoted and was not hit"* are the
  same observation here. `NONE_CARRIED` (343 episodes) means only that no fill
  followed.
* Maker vs taker per fill — only fee-bearing vs fee-free is recoverable.
* L5's reward/rebate credits per window: not published for another account.
* Any price at an instant L5 did not trade. Every price in this document is the
  price of one of L5's own prints, never a mid and never a touch.

## Status

BTC15m only, as directed. Findings, not proposals. The frozen PMUS sweep
(`analysis/l5/sweep/`) is untouched, no risk limit is changed, no executor is
started, and the live probe remains stopped.

# Cell C early-path discriminator study — read-only, no rule, Cell C unchanged

Frozen Cell C re-runs identically with the added instrumentation (gross
**+10.0346**, net **−0.04948**/window, 300 episodes), so no trade moved. No
C2, no threshold search, no scaling, no live orders. Horizons **1 / 2 / 5 /
10 / 20 s** were fixed in advance as the tape's natural scales and are not
chosen by result. Features are built from books and trades with timestamp
≤ entry + H, and **the label is attached only afterwards**.

## A methodological correction I had to make, and it changes the answer

My first pass scored every episode at every horizon. That is wrong: an
episode that exited at 5 s has no state at 20 s that is not *after its own
outcome*, so reading the tape there leaks the future. Scored that way, the
20 s AUC came out at 0.860.

**Scored correctly — on the episodes still open at the horizon, which are
the only ones where a decision exists — the same number is 0.810, and the
early horizons collapse.** Every figure below uses the open population, and
the horizons are therefore *different samples* and not comparable to each
other.

## Decision population at each horizon

| horizon | episodes STILL OPEN at H | productive | sub-fee | gross of the population | fees |
|---|---|---|---|---|---|
| 1s | **280** of 300 | 107 | 173 | +10.4360 | 10.6051 |
| 2s | **268** of 300 | 107 | 161 | +10.7674 | 10.2271 |
| 5s | **231** of 300 | 104 | 127 | +11.7469 | 9.0526 |
| 10s | **185** of 300 | 96 | 89 | +12.7563 | 7.1341 |
| 20s | **130** of 300 | 82 | 48 | +11.9275 | 4.8900 |

An episode that has already exited is not a decision, so each horizon is scored on the episodes still open at it. Horizon rows are therefore NOT comparable to one another as samples.

## Early continuation, within the open population only
## Feature medians, productive / sub-fee, within the open population

| feature (median, productive / sub-fee) | 1s | 2s | 5s | 10s | 20s |
|---|---|---|---|---|---|
| `dprice` | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0100 / 0.0000 | 0.0300 / 0.0100 | 0.0400 / 0.0100 |
| `mfe_to_date` | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0100 / 0.0000 | 0.0300 / 0.0100 | 0.0500 / 0.0125 |
| `mae_to_date` | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0000 / -0.0050 | 0.0000 / -0.0100 |
| `made_new_high` | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 1.0000 / 0.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| `n_new_highs` | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 1.0000 / 0.0000 | 2.5000 / 1.0000 | 4.0000 / 2.0000 |
| `t_first_new_high` | 1.0000 / 1.0000 | 2.0000 / 2.0000 | 2.2117 / 5.0000 | 2.3099 / 5.0299 | 2.8294 / 5.4269 |
| `dbid` | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0000 / 0.0000 |
| `dask` | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0000 / 0.0000 |
| `spread` | 0.0100 / 0.0100 | 0.0100 / 0.0100 | 0.0100 / 0.0100 | 0.0100 / 0.0100 | 0.0100 / 0.0100 |
| `dbid_qty` | 0.0000 / -22.2500 | 0.0000 / -89.8200 | 38.2000 / -67.2700 | 126.7050 / -113.9500 | 1.1200 / -23.5450 |
| `dask_qty` | -120.0000 / 101.6400 | -39.5500 / 46.6900 | 0.0000 / 61.0000 | -2.3550 / 33.2000 | 8.1800 / -15.0150 |
| `trade_imbalance` | 0.0000 / -0.0556 | -0.2668 / -0.1838 | -0.4291 / -0.1723 | -0.3249 / -0.2439 | -0.2627 / -0.2771 |
| `n_prints` | 3.0000 / 4.0000 | 8.0000 / 9.0000 | 19.5000 / 21.0000 | 44.0000 / 44.0000 | 81.0000 / 102.0000 |
| `print_qty` | 91.9800 / 84.3000 | 221.3200 / 262.5500 | 841.6300 / 753.7900 | 2192.3200 / 1755.1100 | 4657.5550 / 4525.6650 |
| `fav_price_now` | 0.7550 / 0.6950 | 0.7650 / 0.6750 | 0.7750 / 0.6600 | 0.7850 / 0.7050 | 0.8150 / 0.7100 |
| `dist_from_50` | 0.2550 / 0.1950 | 0.2650 / 0.1750 | 0.2750 / 0.1600 | 0.2850 / 0.2050 | 0.3150 / 0.2100 |
| `tte_s` | 479.0000 / 479.0000 | 478.0000 / 536.9000 | 475.0000 / 535.0000 | 470.0500 / 530.0000 | 460.0500 / 580.0000 |
| `adds_by_h` | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| `unrealized_per_share` | -0.0100 / -0.0100 | -0.0100 / -0.0100 | 0.0000 / -0.0100 | 0.0200 / 0.0000 | 0.0300 / 0.0000 |
## Early continuation, within the open population only

| horizon | group | n | % productive | mean gross | mean net | gross/fees |
|---|---|---|---|---|---|---|
| 1s | continued (Δ>0) | 77 | 45.5% | +0.0537 | +0.0173 | 1.473 |
| 1s | flat (Δ=0) | 151 | 36.4% | +0.0341 | -0.0026 | 0.928 |
| 1s | reversed (Δ<0) | 52 | 32.7% | +0.0220 | -0.0212 | 0.509 |
| 2s | continued (Δ>0) | 89 | 48.3% | +0.0511 | +0.0162 | 1.466 |
| 2s | flat (Δ=0) | 119 | 40.3% | +0.0497 | +0.0128 | 1.347 |
| 2s | reversed (Δ<0) | 60 | 26.7% | +0.0051 | -0.0405 | 0.113 |
| 5s | continued (Δ>0) | 111 | 55.9% | +0.0750 | +0.0349 | 1.871 |
| 5s | flat (Δ=0) | 70 | 35.7% | +0.0213 | -0.0128 | 0.625 |
| 5s | reversed (Δ<0) | 50 | 34.0% | +0.0386 | -0.0058 | 0.869 |
| 10s | continued (Δ>0) | 118 | 59.3% | +0.0866 | +0.0475 | 2.214 |
| 10s | flat (Δ=0) | 33 | 48.5% | +0.0282 | +0.0051 | 1.219 |
| 10s | reversed (Δ<0) | 34 | 29.4% | +0.0472 | -0.0044 | 0.915 |
| 20s | continued (Δ>0) | 93 | 67.7% | +0.1129 | +0.0728 | 2.812 |
| 20s | flat (Δ=0) | 26 | 57.7% | +0.0243 | +0.0076 | 1.457 |
| 20s | reversed (Δ<0) | 11 | 36.4% | +0.0723 | +0.0066 | 1.101 |

## OOS discrimination within the open population, split BY WINDOW

| horizon | n train / test | always-SUB_FEE baseline | best single feature (TRAIN) | OOS AUC | all-feature logit OOS AUC | OOS accuracy |
|---|---|---|---|---|---|---|
| 1s | 191 / 89 | 0.596 | `unrealized_per_share` | 0.594 | **0.607** | 0.629 |
| 2s | 185 / 83 | 0.566 | `unrealized_per_share` | 0.543 | **0.521** | 0.602 |
| 5s | 165 / 66 | 0.485 | `unrealized_per_share` | 0.619 | **0.665** | 0.636 |
| 10s | 129 / 56 | 0.429 | `unrealized_per_share` | 0.671 | **0.699** | 0.661 |
| 20s | 86 / 44 | 0.364 | `unrealized_per_share` | 0.773 | **0.810** | 0.727 |

## Termination counterfactual — ATTRIBUTION ONLY, not a strategy

If every episode still open at H were terminated at the contemporaneous executable price (taker, fee charged), leaving earlier-exited episodes untouched:

| horizon | episodes terminated | gross | fees | net | Δnet vs frozen C | fees avoided | future excursion sacrificed |
|---|---|---|---|---|---|---|---|
| 1s | 280 | -3.6541 | 10.9861 | -14.6402 | **-13.2555** | +0.4341 | +13.6896 |
| 2s | 268 | -3.1866 | 10.9634 | -14.1500 | **-12.7653** | +0.4568 | +13.2221 |
| 5s | 231 | -2.0961 | 10.9282 | -13.0243 | **-11.6396** | +0.4920 | +12.1316 |
| 10s | 185 | +0.4641 | 10.8402 | -10.3761 | **-8.9915** | +0.5800 | +9.5715 |
| 20s | 130 | +3.1198 | 10.7538 | -7.6340 | **-6.2493** | +0.6664 | +6.9157 |

frozen Cell C for reference: gross +10.0355, fees 11.4202, net -1.3847

These rows are NOT a menu. Terminating early would change which episodes the controller subsequently opens, which this arithmetic does not model; it holds the rest of the frozen run fixed on purpose so the two components -- fees avoided and excursion sacrificed -- can be read separately.

## The primary question, answered

> Before paying most of the episode's eventual turnover, is there observable
> evidence separating the 107 productive episodes from the 193 sub-fee ones?

**At 1–2 s, essentially no.** OOS AUC is **0.607** and **0.521** against
baselines of 0.596 and 0.566 — the 2 s model is indistinguishable from
guessing the majority class, and the 1 s model beats it by one point of
AUC. At those horizons the population still holds **$10.44 / $10.77 of the
$10.03 gross and $10.61 / $10.23 of the $11.42 fees**: essentially the whole
economics is still ahead, and essentially nothing is yet visible.

**Discrimination appears only at 10–20 s** (AUC 0.699, 0.810) — and by then
the sample is self-selected by survival, the base rate has flipped from 36%
productive to 63%, and only $4.89 of the fee bill is still unspent. That is
the survivorship problem in its exact quantitative form, not a way around
it.

The single most informative feature at every horizon is
`unrealized_per_share` — the position's own mark. Which is to say: the best
early predictor of whether an episode will pay for itself is whether it is
already winning.

## Early continuation does have genuine content — but it is weak when it matters

| horizon | continued (Δ>0) | reversed (Δ<0) |
|---|---|---|
| 1 s | 45.5% productive, gross/fees 1.473 | 32.7%, 0.509 |
| 2 s | 48.3%, 1.466 | **26.7%, 0.113** |
| 5 s | 55.9%, 1.871 | 34.0%, 0.869 |
| 10 s | 59.3%, 2.214 | 29.4%, 0.915 |
| 20 s | 67.7%, 2.812 | 36.4% (n=11), 1.101 |

The direction is real and monotone in the right way at every horizon: an
episode that continues immediately is roughly 1.4–1.9× as likely to clear
its own fee as one that reverses. But at the horizons where acting would
save anything, the gap is **45.5% vs 32.7%** — a tilt, not a separator, and
`made_new_high` has an identical median (0.0) in both classes at 1 s and 2 s.

## Termination counterfactual — ATTRIBUTION ONLY, not a strategy

If every episode still open at H were terminated at the contemporaneous executable price (taker, fee charged), leaving earlier-exited episodes untouched:

| horizon | episodes terminated | gross | fees | net | Δnet vs frozen C | fees avoided | future excursion sacrificed |
|---|---|---|---|---|---|---|---|
| 1s | 280 | -3.6541 | 10.9861 | -14.6402 | **-13.2555** | +0.4341 | +13.6896 |
| 2s | 268 | -3.1866 | 10.9634 | -14.1500 | **-12.7653** | +0.4568 | +13.2221 |
| 5s | 231 | -2.0961 | 10.9282 | -13.0243 | **-11.6396** | +0.4920 | +12.1316 |
| 10s | 185 | +0.4641 | 10.8402 | -10.3761 | **-8.9915** | +0.5800 | +9.5715 |
| 20s | 130 | +3.1198 | 10.7538 | -7.6340 | **-6.2493** | +0.6664 | +6.9157 |

frozen Cell C for reference: gross +10.0355, fees 11.4202, net -1.3847

These rows are NOT a menu. Terminating early would change which episodes the controller subsequently opens, which this arithmetic does not model; it holds the rest of the frozen run fixed on purpose so the two components -- fees avoided and excursion sacrificed -- can be read separately.

## The counterfactual's real finding, which is not about horizons

Every horizon is catastrophic — Δnet **−$13.26** at 1 s through **−$6.25**
at 20 s — but the reason matters far more than the ranking:

| | |
|---|---|
| fees avoided by terminating | **+$0.43 to +$0.67** |
| future excursion sacrificed | **−$13.69 to −$6.92** |

**Early termination barely saves any fees at all.** The entry fee is already
paid before any horizon is reached, and terminating *still pays a taker exit
fee* — so the only saving is the handful of later additions that never
happen, 4–6% of the fee bill. Meanwhile the trail's patience is where
essentially all of the gross lives.

So the lever the anatomy seemed to point at — *stop spending round-trip fees
on low-value episodes* — **cannot be pulled after entry.** By then the money
is committed in both directions. The only fee that can actually be avoided
is one never incurred, which makes **entry selection** the live question,
not exit timing.

And entry selection is the thing this study and the anatomy both measured as
weak: pre-entry observables separate at gross/fees 0.36 → 1.56 across their
whole range, and the first two seconds after entry add about one point of
AUC over guessing.

## What is established, and what is not

**Established:** the early path carries a real, correctly-signed tilt
(continuation ≈ 1.4–1.9× the productive rate of reversal), the tilt is too
weak at 1–2 s to act on, and blanket early termination destroys the strategy
because fees are front-loaded and symmetric while the gross is back-loaded.

**Not established, and not attempted:** any horizon, any threshold, any
filter. No horizon was chosen by which counterfactual loses least — all five
lose, and I am reporting the mechanism rather than ranking them.

Cell C is unchanged. `historical replay` throughout; verified compensation
$0.0000.

# What decides whether L5 ever pairs a leg

2216 windows with a first leg — **1610 paired (72.7%)**, 606 carried to settlement.

> This history is entirely BTC and ETH up/down windows of 5 and 15 minutes. Our lane trades sports. Time-to-work and far-side liquidity are not crypto-specific mechanisms, but every magnitude here is measured on crypto and none of it is a sports measurement.

## Predictive features

known at the moment the residual state opens, so these can inform selection: they exist before the outcome does

### window length minutes

| bucket | n | completed | rate |
|---|---|---|---|
| 15 | 556 | 519 | 93.3% |
| 5 | 1660 | 1091 | 65.7% |

### window remaining at first leg

| bucket | n | completed | rate |
|---|---|---|---|
| 0.0-0.2 | 71 | 27 | 38.0% |
| 0.2-0.4 | 190 | 80 | 42.1% |
| 0.4-0.6 | 310 | 183 | 59.0% |
| 0.6-0.8 | 441 | 322 | 73.0% |
| 0.8-1.0 | 1204 | 998 | 82.9% |

### first leg price

| bucket | n | completed | rate |
|---|---|---|---|
| 0.0-0.1 | 95 | 27 | 28.4% |
| 0.1-0.2 | 114 | 77 | 67.5% |
| 0.2-0.3 | 109 | 88 | 80.7% |
| 0.3-0.4 | 142 | 114 | 80.3% |
| 0.4-0.5 | 315 | 280 | 88.9% |
| 0.5-0.6 | 494 | 421 | 85.2% |
| 0.6-0.7 | 408 | 311 | 76.2% |
| 0.7-0.8 | 257 | 156 | 60.7% |
| 0.8-0.9 | 184 | 86 | 46.7% |
| 0.9-1.0 | 98 | 50 | 51.0% |

### market imbalance at first leg

| bucket | n | completed | rate |
|---|---|---|---|
| balanced 0.35-0.65 | 1143 | 963 | 84.2% |
| leaning 0.20-0.35 / 0.65-0.80 | 606 | 419 | 69.1% |
| lopsided <0.20 or >0.80 | 467 | 228 | 48.8% |

## Realized features

observed while the window ran. NOT offered as a filter -- a window with more actions had more time and more flow, and a window that completed had more of everything by construction

### opening side distinct prices

| bucket | n | completed | rate |
|---|---|---|---|
| 1 | 459 | 169 | 36.8% |
| 2 | 449 | 271 | 60.4% |
| 3 | 278 | 207 | 74.5% |
| 4+ | 1030 | 963 | 93.5% |

### Does it survive its confounders?

Holding window length and time remaining fixed, the direction holds in **4 of 5** strata that have at least two usable cells.

| stratum (length \| remaining) | 1 | 2 | 3 | 4+ |
|---|---|---|---|---|
| 15 | 0.0-0.2 | — | — | — | — |
| 15 | 0.2-0.4 | — | — | — | — |
| 15 | 0.4-0.6 | — | — | — | — |
| 15 | 0.6-0.8 | — | — | — | 95% (n=21) |
| 15 | 0.8-1.0 | — | — | — | 99% (n=465) |
| 5 | 0.0-0.2 | 33% (n=33) | 41% (n=22) | — | — |
| 5 | 0.2-0.4 | 26% (n=92) | 50% (n=50) | 71% (n=21) | 67% (n=15) |
| 5 | 0.4-0.6 | 41% (n=96) | 58% (n=73) | 72% (n=54) | 82% (n=68) |
| 5 | 0.6-0.8 | 42% (n=77) | 69% (n=124) | 84% (n=67) | 85% (n=142) |
| 5 | 0.8-1.0 | 43% (n=135) | 61% (n=167) | 72% (n=115) | 93% (n=299) |

## Findings

1. TIME TO WORK IT IS THE STRONGEST PREDICTOR. L5 pairs 93.3% of 15-minute windows and 65.7% of 5-minute ones, and within a window the rate falls from 82.9% when he opens with 80-100% of it left to 38.0% when he opens with under 20% left.
2. IMBALANCE IS THE SECOND. A first leg in a balanced book (0.35-0.65) pairs 74.6% of the time on 5-minute windows and 97.7% on 15-minute ones; a lopsided one (<0.20 or >0.80) pairs 49.7% and 33.3%. There is nothing on the far side to pair against.
3. WORKING THE POSITION SURVIVES ITS CONFOUNDERS. One opening-side price completes 36.8% of the time and four or more completes 93.5%, and that gap does not collapse when window length and time remaining are held fixed -- inside the largest stratum it still runs 43.0% to 93.3%. It is evidence, not proof: a market active enough to reprice into completes for reasons of its own.

## No causal claim

These are conditional rates with two confounders held fixed, not an experiment. They say where completion happens, not what would happen if we changed our behaviour.

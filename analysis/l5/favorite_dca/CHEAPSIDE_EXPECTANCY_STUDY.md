# Cheap-side expectancy and calibration — it does not survive

Expectancy/calibration study, **not** a classifier and **not** a
controller. Nothing is optimized and no cutoff, price band or time band is
selected. The record correction (`RESEARCH_RECORD_CORRECTION.md`) is
committed, and `CELL_C_CAPACITY_REPORT.md` and
`CF_MATCH_EXTEND_ATTRIBUTION.md` are frozen.

## Design

The opportunity set comes from the **tape, not from any strategy**: every
10 seconds of every eligible window is one observation, so the sample does
not inherit CF_MATCH's action selection. At each instant both outcomes are
priced at their executable buy price — UP at the ask, DOWN at `1 − bid` —
the cheaper is taken, and settlement answers whether it paid. **2,464
observations, 28 windows.** Instants where the venue served a one-sided
book are skipped, not imputed.

The quantity is realized value: `EV/share = 1{wins} − price − taker fee`.

**The statistical hazard, stated before the numbers:** settlement is ONE
binary draw per window, so 2,464 samples inside 28 windows carry roughly
28 independent observations. **Every interval below is a window-level
bootstrap** (2,000 resamples of whole windows). Sample-level intervals
would be about 9× too narrow and would make all of this look significant.

| executable price band | n | windows | **empirical P(pays $1)** | mean price | fee | **EV per share** | 95% CI (window boot) | EV per $ |
|---|---|---|---|---|---|---|---|---|
| $0.01–$0.05 | 568 | 28 | **0.026** | 0.0158 | 0.0011 | **+0.0095** | [-0.0175, +0.0773] | +0.605 |
| $0.05–$0.10 | 223 | 28 | **0.045** | 0.0714 | 0.0046 | **-0.0311** | [-0.0754, +0.0409] | -0.436 |
| $0.10–$0.20 | 327 | 28 | **0.205** | 0.1436 | 0.0085 | **+0.0528** | [-0.0898, +0.2103] | +0.367 |
| $0.20–$0.30 | 341 | 28 | **0.343** | 0.2457 | 0.0128 | **+0.0845** | [-0.0864, +0.2572] | +0.344 |
| $0.30–$0.40 | 411 | 28 | **0.406** | 0.3492 | 0.0157 | **+0.0413** | [-0.1261, +0.2023] | +0.118 |
| $0.40–$0.50 | 529 | 28 | **0.405** | 0.4434 | 0.0171 | **-0.0559** | [-0.1506, +0.0389] | -0.126 |
| **all cheap-side** | 2464 | 28 | **0.255** | 0.2298 | 0.0103 | **+0.0147** | [-0.0627, +0.0993] | +0.064 |

**Calibration is not badly off.** Empirical P(pays) against mean price:
0.026 vs 0.016, 0.045 vs 0.071, 0.205 vs 0.144, 0.343 vs 0.246, 0.406 vs
0.349, 0.405 vs 0.443. Some bands look underpriced — but **every single
band's confidence interval contains zero**, and the widest is ±0.17 on a
point estimate of +0.08.

## The answer: the +$47.32 is a 28-window sample artifact

### Leave-best-window robustness

| | total EV (per-share units) | per share |
|---|---|---|
| all 28 windows | +36.308 | +0.01474 |
| leave-best-1 | -8.791 | -0.00370 |
| leave-best-3 | -85.711 | -0.03901 |
| windows with positive total EV | 10 of 28 | |
| best / worst window contribution | +45.10 / -28.00 | |

**Removing a single window flips the sign.** +36.31 → **−8.79**.
Leave-best-3 → **−85.71**. Only **10 of 28 windows** are positive, and the
best contributes +45.10 against the worst at −28.00 — one window is larger
than the entire aggregate.

This is exactly the failure mode you named in advance: a few longshot
payouts making a backtest look extraordinary. It does, and they are.

## The region CF_MATCH pointed at, tested rather than assumed

### The descriptive region CF_MATCH pointed at (reported, not selected)

| region | n | windows | P(pays) | mean price | **EV per share** | 95% CI | EV per $ |
|---|---|---|---|---|---|---|---|
| cheap only (<$0.20) | 1118 | 28 | 0.082 | 0.0643 | **+0.0141** | [-0.0448, +0.0999] | +0.219 |
| recently fell (30s move < 0) | 1339 | 28 | 0.257 | 0.2323 | **+0.0139** | [-0.0652, +0.0984] | +0.060 |
| recently lost leadership (<=60s) | 540 | 27 | 0.380 | 0.4089 | **-0.0455** | [-0.0985, +0.0061] | -0.111 |
| cheap AND fell | 595 | 28 | 0.104 | 0.0795 | **+0.0199** | [-0.0523, +0.1122] | +0.250 |
| cheap AND fell AND lost leadership | 25 | 8 | 0.080 | 0.1468 | **-0.0754** | [-0.1649, +0.0017] | -0.514 |
| >600s remaining | 784 | 28 | 0.421 | 0.3641 | **+0.0415** | [-0.0861, +0.1792] | +0.114 |
| <=120s remaining | 336 | 28 | 0.012 | 0.0458 | **-0.0364** | [-0.0625, -0.0119] | -0.796 |

`cheap AND fell` — the region the attribution pointed at — is **+$0.0199
per share with a CI of [−0.052, +0.112]**, and leave-best-1 takes it to
**−0.0028**. It does not survive.

`cheap AND fell AND lost leadership` is **INSUFFICIENT**: 25 observations
across 8 windows. Not evidence in either direction.

### Leave-best-window on the regions

| region | EV/share all | leave-best-1 | leave-best-3 | sign survives? |
|---|---|---|---|---|
| all cheap-side | +0.01474 | -0.00370 | -0.03901 | **no** |
| cheap (<$0.20) | +0.01407 | -0.00818 | -0.04076 | **no** |
| cheap AND fell | +0.01987 | -0.00276 | -0.04741 | **no** |
| $0.20–$0.30 | +0.08455 | +0.03634 | -0.06333 | **no** |
| >600s remaining | +0.04151 | +0.01767 | -0.03151 | **no** |
| <=120s remaining | -0.03644 | -0.04377 | -0.04641 | negative throughout |

## First opportunity vs repeated additions — and this inverts the attribution


| run position | n | windows | P(pays) | mean price | EV per share | 95% CI | EV per $ | leave-best-1 | leave-best-3 |
|---|---|---|---|---|---|---|---|---|---|
| 1st (side just became cheap) | 137 | 28 | 0.445 | 0.4595 | **-0.0313** | [-0.0584, -0.0103] | -0.068 | -0.03451 | -0.04073 |
| 2nd–3rd | 194 | 28 | 0.387 | 0.4177 | **-0.0476** | [-0.1099, +0.0062] | -0.114 | -0.06120 | -0.08316 |
| 4th–10th | 443 | 28 | 0.339 | 0.3480 | **-0.0242** | [-0.1160, +0.0514] | -0.070 | -0.04299 | -0.07562 |
| 11th+ | 1690 | 28 | 0.202 | 0.1586 | **+0.0358** | [-0.0498, +0.1413] | +0.226 | +0.01253 | -0.03354 |

On the exogenous grid, the **first** moment a side becomes cheap is
**−$0.0313 per share with a CI of [−0.058, −0.010] that excludes zero** —
reliably *negative* — while the deep-run opportunities (11th+, mean price
$0.159) carry the positive point estimate. That is the **opposite** of the
CF_MATCH attribution's "first-in-run +$0.077/$, later −$0.44 to −$0.68".

The two are not in conflict once you see what each conditions on.
CF_MATCH's run counter advanced on *its own purchases*, which toggled only
at reversals, so its "first in run" meant "just after a reversal, buying
the newly-cheapened side at ~$0.46"; here the grid's "11th+" means "a side
that has been cheap for a while", i.e. cheaper still. **Both statements
reduce to the same thing: price, not position.** And on price the honest
reading is the top table, where every band's interval contains zero.

## The one robust result, and it is negative

**Cheap-side purchases with ≤120 s remaining: −$0.0364 per share, CI
[−0.0625, −0.0119], excludes zero, negative at leave-best-1 and
leave-best-3.** P(pays) is 0.012 against a mean price of $0.046 — late
longshots are reliably overpriced. That is the only statistically solid
finding in this study, and it is a thing not to do.

## Verdict

**No robust OOS positive expectancy exists in the cheap side on this
evidence.** Therefore, per the condition you set, **no new controller is
preregistered.** `BUY BOTH → EXTEND VALUE/CHEAP SIDE → SWITCH` is not
licensed by this data, and the attribution's +$47.32 should be read as what
the leave-one-out test says it is: one window.

What the study does establish:

1. PMUS BTC15m cheap-side prices are **roughly calibrated** — no systematic
   longshot bias large enough to clear fees was detected at any price band;
2. the 28-window tape **cannot resolve** effects of this size — a single
   binary settlement per window gives ~28 effective observations against
   point estimates whose CIs span ±0.1 per share;
3. settling this question needs **more windows, not more analysis**. At the
   observed dispersion, distinguishing a +$0.02/share edge from zero at the
   95% level needs on the order of several hundred windows. The recorder is
   live and produces 96/day, so that is weeks of collection, not a new
   method.

`historical replay`. No scaling, no live orders, no controller, no cutoff
selected. Verified compensation $0.0000.

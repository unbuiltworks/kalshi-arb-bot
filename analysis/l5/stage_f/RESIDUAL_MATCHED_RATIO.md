# The 0.3137 vs 0.9530 residual-to-matched ratio

**Date:** 2026-11-01
**Question:** how does L5 hold residual inventory to 0.3137 of matched while we
run 0.9530, and can a comparable policy cut our residual losses without cutting
matched profit?
**Answer:** the gap is not a skill gap. It is a fill-count and market-duration
artifact. The one order-time rule that does separate residual from matched is
**already deployed in our engine, and slightly tighter than the validated
optimum.** No new rule survives. The blocking observation is named in §7.

Status labels per `CLAUDE.md`: L5 figures are **chronological history**; our
figures are **settled/realized** from the closed eight-window campaign; the
gate result is **historical replay** and is labelled as such throughout.

---

## 1. The comparison was never like-for-like

`analysis/l5/l5_windows_summary.csv`, 1,610 L5 windows with `matched_qty > 0`
and at least 2 fills, split by market duration:

| book | windows | median fills/window | median residual/matched |
|---|---|---|---|
| L5 5-minute | 1,091 | 6.0 | **0.9500** |
| L5 15-minute | 519 | 123.0 | **0.1975** |
| **ours** (settled) | 7 | **3.17** | **0.9530** |

The 0.3137 headline is L5's 15-minute book at ~123 fills per window. Our 0.9530
is a 3.17-fill book. **L5's own 5-minute book runs 0.9500 at 6 fills.**

Narrowed to his windows at our fill count:

| cohort | N | median ratio | p25 | p75 | percentile of our 0.9530 |
|---|---|---|---|---|---|
| L5 5m, n=2–4 | 354 | 1.0173 | 0.4019 | 2.4434 | **44.1** |
| L5 5m, n=2–8 | 828 | 1.0282 | 0.4100 | 2.3618 | **45.9** |
| L5 15m, all | 519 | 0.1975 | 0.0854 | 0.4142 | 89.4 |

Our ratio is at the **44th percentile** of L5's own windows at our fill count —
the middle of his distribution, marginally better than his median. The deficit
appears only against a cohort operating at 39× our fill count.

## 2. The ratio obeys a square-root law, so it is not a sequencing skill

Define `k = (residual/matched) · sqrt(fills)`. If a balanced-intent process takes
n steps, matched grows ~n and the imbalance ends ~sqrt(n) from balance, so the
ratio falls as `k/sqrt(n)` with k constant. Across L5's fill-count deciles:

| decile | windows | median fills | median ratio | median k |
|---|---|---|---|---|
| 1 | 161 | 2 | 1.0031 | 1.732 |
| 2 | 161 | 4 | 1.0000 | 1.862 |
| 5 | 161 | 7 | 0.9322 | 2.478 |
| 8 | 161 | 23 | 0.2988 | 1.538 |
| 9 | 161 | 131 | 0.2008 | 2.215 |
| 10 | 161 | 230 | 0.1352 | 2.072 |

Over a **115× range in fill count** the raw ratio collapses 7.4× (1.003 → 0.135)
while k stays inside 1.5–2.5 with no trend. The ratio decline *is* the step-count
law, not a repair policy.

k is also stable across a ~87× change in his capital (median k: March 2.080,
April 1.012, August 2.195), which a genuine sequencing skill would not be —
but a step-count law is, because he scaled fills with capital.

**Our k = 0.9530 · sqrt(3.17) = 1.697**, against an L5 all-window median of
**2.112**. On the fill-count-adjusted metric **we are already better than his
median and better than every decile except his lowest (1.732).** Our sequencing
is not the defect; our step count is, and step count is capital.

*Honest caveat:* k's cross-window dispersion is wide (IQR 0.83–4.66) and its
mean is distorted by small-matched windows. The claim rests on the decile
medians being trendless, not on k being a tight constant.

## 3. Within-window evolution (request item 3)

L5's within-window imbalance by share of the window's fills:

| % of fills elapsed | imbalance | k |
|---|---|---|
| 10 | 0.359 | 1.258 |
| 50 | 0.103 | 0.807 |
| 90 | 0.083 | 0.872 |
| 100 | 0.098 | 1.086 |

Raw imbalance falls 4.3× while k moves 1.258 → 1.086. The intra-window decline
is the same sqrt law operating as fills accumulate — **not a repair rule firing
late in the window.** Our trajectory is the mirror image: we open near flat, one
leg fills, and the window *ends* at 0.95 because no further steps arrive.

## 4. A low ratio is a marker, not a lever

Within fill-count strata, a low *realized* ratio does predict higher profit
(y = P&L per $100 bought), consistently and out-of-sample:

| book | stratum | N | low-ratio half y | high-ratio half y | delta |
|---|---|---|---|---|---|
| 5m | n=2–4 | 354 | +8.809 | −2.772 | **+11.581** |
| 5m | n=5–8 | 474 | +3.147 | +0.535 | +2.611 |
| 5m | n=9–30 | 263 | +1.225 | −7.639 | +8.864 |
| 15m | n=9–30 | 116 | +3.995 | −5.432 | +9.427 |
| 15m | n>30 | 370 | +1.851 | −1.605 | +3.456 |

Chronological split confirms the sign holds out of sample (5m: +7.241 in-sample,
**+7.386 out-of-sample**; 15m: +5.834 → **+3.371**).

**This is not an edge.** The ratio is realized *ex post*, and the correlation is
mechanically entailed by adverse residual selection, which we already measured
independently at **−0.2315 per unit over 384 windows**. "Windows that ended
balanced made money" restates "residual loses money." It identifies no choice
available at order time. Acting on it would repeat the error of the rejected
imbalance gate, which bound on nothing.

Note also the Simpson reversal: *across* strata the relationship inverts — L5's
5m windows at n=9–20 have a far better ratio (0.6293) and far worse economics
(−3.420/$100) than his n=2–4 windows (ratio 1.0173, **+0.891**/$100). High fill
counts in a 5-minute market mark windows where he was chasing a moving price.
Optimizing the ratio directly would have made his book worse.

## 5. The one order-time rule that does separate the two

The only lever observable at order time that moves residual and matched
*differently* is the clock. Replaying L5's 556-window 15m tape
(`l5_canonical_sequence.csv.gz`) and refusing every imbalance-**increasing**
fill after a fraction of the window — **historical replay, not realized**:

| gate | total P&L | matched | residual | ratio | P&L/$100 |
|---|---|---|---|---|---|
| none | −359.97 | 556,458 | 104,962 | 0.1886 | −0.063 |
| 80% | +819.45 | 477,345 | 44,984 | 0.0942 | +0.169 |
| **70%** | **+1210.55** | 418,784 | 26,079 | 0.0623 | **+0.286** |
| 60% | +485.39 | 357,045 | 13,358 | 0.0374 | +0.135 |
| 50% | +390.39 | 297,688 | 7,130 | 0.0240 | +0.130 |

Residual falls **4.0×** while matched falls only **1.33×**. This is the only
intervention found this session that moves them at different rates.

### Chronological out-of-sample (request item 5)

Gate selected on the early 278 windows, evaluated on the held-out late 278:

| gate | in-sample P&L/$100 | **out-of-sample P&L/$100** |
|---|---|---|
| none | +0.313 | −0.156 |
| 80% | +0.874 | −0.001 |
| **70%** | **+1.164** | **+0.070** |
| 60% | +1.085 | −0.103 |
| 50% | +1.124 | −0.124 |

The 70% gate is the in-sample optimum **and the only gate value positive out of
sample**: +0.070/$100 against a −0.156/$100 no-gate baseline, an improvement of
**+0.226 per $100 deployed**.

*Replay limitations, stated plainly:* the counterfactual assumes a refused leg
simply does not happen and nothing else changes. L5 trades at 2.39× average
fill size, so withdrawing his late legs would in reality move the book. The
result is directional evidence about the clock, not a P&L forecast.

## 6. The rule is already deployed, and tighter than the optimum

`src/l5_two_sided_probe.py:791`

```
ENTRY_CUTOFF_S = 600.0      # offset from window START
COMPLETE_CUTOFF_S = 720.0
```

600s of a 900s window = **66.7%**, against the validated 70%. The engine already
separates the two decisions for exactly this reason, and the knob is
**raise-only in the safe direction** (`_earlier_only`): it cannot be loosened by
configuration at all.

**I do not recommend moving it to 630s.** The out-of-sample grid's next point
down (60%) is negative, so the grid cannot resolve 600 from 630; the change is
risk-increasing; and it would require a code change to a safety knob
deliberately built to refuse that direction.

**So the answer to request item 4 is: yes, such a policy exists, it is the
late-leg entry cutoff, and we already run it.** There is nothing to implement.

### Magnitude at our capital

| quantity | value |
|---|---|
| OOS gate improvement | +0.226 per $100 deployed |
| our deployment | ~$1.74 per window |
| **effect at our size** | **+$0.0039 per window** |
| windows to detect at 80% power | **618,288** (6,440 days) |

Even the one validated rule is worth **0.4 cents a window** to us, and we
already have it.

## 7. Power recheck — correcting my own 760-window figure

My earlier figure tested the joint-fill **proportion** and assumed independent
entries. Corrected (α=0.05 two-sided, 80% power, 2.14 entries/window):

| test | assumptions | windows required | days at 96/window-day |
|---|---|---|---|
| proportion, independent entries *(my old figure)* | — | ~760 | ~8 |
| proportion + clustering ICC 0.5 (DE 1.57) + 1.20 sequential | realistic | **1,434** | 14.9 |
| proportion + clustering ICC 1.0 (DE 2.14) + 1.20 sequential | conservative | **1,956** | 20.4 |
| **net P&L/window**, detect +$0.25 | sd = 1.10432 | 153 | 1.6 |
| **net P&L/window**, detect +$0.10 | sd = 1.10432 | **956** | 10.0 |
| **net P&L/window**, detect +$0.05 | sd = 1.10432 | 3,824 | 39.8 |
| **net P&L/window**, reach break-even (+$0.0108) | sd = 1.10432 | **81,971** | **854** |

Per-window settled P&L from the seven closed windows: −0.842, +1.127, +1.213,
+1.165, −0.949, −0.970, −0.819; mean **−0.0108**, sd **1.10432**. The variance is
the ±$1 residual lump, not the fill rate.

**I understated the requirement by 2× to 100×.** These are one-sample figures
against a known baseline; a two-arm A/B doubles them. Confirming mere break-even
needs **854 days** of continuous eight-window operation.

## 8. Verdict and the blocking observation

**Strongest validated correction:** the premise is wrong. The 0.3137 vs 0.9530
gap measures market duration and fill count, not behavior. L5's 5m book at our
fill count runs 0.95–1.03; our 0.9530 is at its 44th percentile; and
fill-count-adjusted, our k=1.697 beats his median 2.112. **There is no
residual-reduction skill in L5's tape for us to copy.** The one real lever is
the late-leg clock, it is already deployed at 66.7% against a validated 70%, and
its value at our capital is +$0.0039/window.

This is consistent with, and does not depend on, the earlier NO EDGE finding —
which remains statistically inconclusive at 15 entries, exactly as stated.

**The missing observation that prevents going further:** L5's residual/matched
ratio *in 15-minute windows at our fill count*. His 15m fill distribution is p05
= 7, p25 = 26, p50 = 123; only **11 of 519** of his 15m windows (2.1%) have ≤4
fills. The two regimes **do not overlap at our operating point**, so no
like-for-like 15m comparison can be constructed from his history. Resolving it
requires one of:

1. **our own 15m windows at his fill density** — needs ≈38× our capital envelope
   (per `FILL_COUNT_DECOMPOSITION.md`, where the 374.5× fill gap decomposed to
   38.8× capital × 9.5× turnover), and that document also showed fill count is
   **P&L-neutral at a fixed envelope**, so this buys the measurement, not profit;
2. **his 5m book as the proxy** — which is what §1 does, and it says our ratio is
   already normal.

Neither path yields a deployable behavioral change. I am not proposing a capital
increase, a new service, a campaign, or a code change on this evidence.

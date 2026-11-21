# Composition diagnostic — read-only, 2026-09-16 18:05Z

**Nothing was deployed, restarted, modified, tuned or filtered.** The official
M0–M5 result stands exactly as preregistered and is untouched by this file.
Nothing here is a maturity PASS. The 100k checkpoint continues unchanged.

## ANSWER: **BOTH**

The decision *rule* does not drift — that is structurally excluded and proven
below. But V1.1 has a genuine **intrinsic dynamic instability**, demonstrated at
constant slate size, **and** the universe composition shifted materially. Both
contribute; neither alone accounts for the M3/M4/M5 failures.

---

## What is NOT derivable, stated before the numbers

The logs were instrumented for the maturity criterion, not for stratification.
`cycle` lines carry no slug; `summarize()` emits aggregates only. The per-row
fields live on the service's volume, which this session cannot read.

**Not computable from reachable data:** sport / category · pregame vs live /
in-play · market type · per-block price bucket · which specific markets left the
universe. These are omitted rather than approximated. Per-block *net* market
arrivals ARE computable; departures are not.

---

## 1. Intrinsic DRIFT of the decision rule is structurally excluded

Stage 1 is a frozen lookup. Measured, not asserted:

```
artifact sha           c176813c250bacd0        constant in every shadow_start
same key, 50 evals  -> 1 distinct p_act        {0.37509622786759045}
act_thresholds         {"0.35": 5000}          in ALL 26 summary windows
restarts in window     0                       M0 validity
```

Therefore `stage1_wait_rate(t) = P_t(p_act(key) < 0.35)` where `p_act(·)` is
**time-invariant by construction**. Only `P_t` — the distribution of evaluated
keys — can move. There is no channel for the mapping itself to drift.

## 2. The control co-moves, and it has no Stage 1 at all

```
corr(V1 ACT , V1.1 ACT)  = +0.898   across the nine 30-minute blocks
```

V1 has no ACT/WAIT stage; its only WAIT is a risk cap. Two controllers moving
together, one of which cannot consult Stage 1, means a **shared driver** — the
population of markets and states being evaluated.

## 3. Per-30-minute blocks

| blk | clock | rows | mkts/cyc | cum mkts | new | V1.1 | V1 | tte obs |
|---|---|---|---|---|---|---|---|---|
| 0 | 13:33 | 4,475 | 49.7 | 138 | 138 | 0.4840 | 0.9625 | 0.94 |
| 1 | 14:03 | 6,258 | 70.3 | 242 | 104 | 0.2693 | 0.9124 | 0.94 |
| 2 | 14:33 | 6,879 | 77.3 | 291 | 49 | 0.1820 | 0.8904 | 0.94 |
| 3 | 15:03 | 5,971 | **67.1** | 358 | 67 | **0.1732** | 0.8334 | 0.93 |
| 4 | 15:33 | 5,768 | 64.1 | 435 | 77 | 0.2387 | 0.8634 | 0.94 |
| 5 | 16:03 | 5,968 | **67.1** | 482 | 47 | **0.2703** | 0.8797 | 0.94 |
| 6 | 16:33 | 6,208 | 69.8 | 560 | 78 | 0.2486 | 0.8813 | 0.93 |
| 7 | 17:03 | 9,405 | 105.7 | 745 | 185 | 0.4656 | 0.9535 | 0.96 |
| 8 | 17:33 | 6,962 | 107.1 | 831 | 86 | 0.3967 | 0.9134 | 0.96 |

```
corr(new markets   , V1.1 ACT) = +0.822      EXOGENOUS arrivals
corr(markets/cycle , V1.1 ACT) = +0.313
corr(residual_cap  , V1.1 ACT) = -0.929      ENDOGENOUS, over 26 windows
```

### The natural experiment that isolates the endogenous part

**Blocks 3 and 5 have identical slate size — 67.1 markets/cycle — and V1.1 ACT
rates of 0.1732 vs 0.2703, a 1.56× swing.** Exogenous scale held constant, the
rate still moved by more than half. That is the intrinsic component, measured.

## 4. Why: the gap bucket, and a threshold sitting inside the mode

ACT rate by key dimension over the 1,344 reachable keys (TTE pinned to the
shadow sentinel), threshold 0.35:

| dimension | spread in ACT rate | exogenous? |
|---|---|---|
| **gap bucket** (`since_last_s`) | **0.583 (gap>30) → 0.955 (gap2-10)** | **no — the controller's own cadence** |
| imbalance (residual) | 0.786 → 0.911 | no — the controller's own fills |
| price | 0.677 → 0.995 | **yes — which markets are in the universe** |
| last_rel | 0.819 → 0.837 | no |
| last_size | 0.810 → 0.845 | no |

**The gap bucket dominates, and it is endogenous.** `ctx.record()` fires only on
a proposal, so the gap since the last action is set by the controller's own
participation: propose less → gaps lengthen → mass moves into `gap>30`, the
lowest-ACT bucket → propose less still. A self-reinforcing loop.

It is amplified because **the median `p_act` stays within ±0.06 of the 0.35
threshold for the entire run** (observed p50 range 0.2907–0.4053). Small
distributional shifts flip a large fraction of rows across the line. Hence V1.1
swings **4.26×** while V1 swings **1.20×**, and the Stage-1 suppression factor
(V1.1 ÷ V1) swings **3.75×**, from 0.176 to 0.660.

### A conflation worth recording, NOT fixed here

`gap_bucket(None) == "gap>30"`. A market the controller has **never acted on**
is keyed identically to one it acted on ten minutes ago. Historically `gap>30`
meant "L5 waited >30 s between its own actions" — it never meant "never traded
here", because the training windows only contain markets L5 was already in.
Every fresh market entering the universe is therefore scored with a key whose
historical meaning is different. This is the prospective face of the known,
already-documented OPEN/entry gap (`p_act` at `last_rel=open`, PR-AUC 0.21).
**Recorded only. Frozen. Not changed, not tuned, not worked around.**

## 5. Same-stratum comparison — the one stratum observable per window

`last_rel` is the only stratum present in every summary window.

```
mix, first window   open 0.396  extend 0.582  complete 0.022
mix, last  window   open 0.285  extend 0.710  complete 0.005
max composition effect from last_rel alone   -0.02 pp
OBSERVED change in V1.1 ACT rate             -26.43 pp
```

`last_rel` mix explains a **negligible** share. The movement lives in the gap,
price and imbalance dimensions, which are not observable per window from the
logs — so a full Kitagawa decomposition over them is **not identifiable from
reachable data**. What *is* identifiable is the intrinsic/composition split,
via §1, §2 and the blocks 3-vs-5 experiment.

## 6. Decomposition, as far as the data supports

| component | evidence | verdict |
|---|---|---|
| intrinsic **drift** of the rule | frozen artifact, deterministic map, constant threshold, 0 restarts | **excluded** |
| intrinsic **dynamic instability** | blocks 3 vs 5: identical 67.1 mkts/cyc, ACT 0.1732 vs 0.2703 (1.56×); gap-bucket feedback; threshold inside the p_act mode | **present** |
| exogenous composition | 49.7 → 107.1 mkts/cyc; new arrivals corr +0.822; V1 co-moves +0.898 | **present** |
| endogenous state composition | residual_cap corr −0.929; `our_inventory` p50 0.22 → 0.71 | **present** |
| finer attribution (sport, pregame/live, type, price) | per-row data on the volume, unreachable | **not identifiable** |

## Consequence for the live decision — unchanged

This diagnostic does not rescue the participation rate. A rate with a
self-reinforcing feedback loop, amplified by a threshold sitting inside its own
mode, is not merely imprecise — it has no single value to estimate under a
changing slate. The 50k recommendation stands: **no live authorization**.

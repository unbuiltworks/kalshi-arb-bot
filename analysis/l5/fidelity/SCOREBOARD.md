# L5_FIDELITY_v0 — first frozen predictions

**EARLY / INSUFFICIENT SAMPLE — n = 4.** Reported now rather than polished
later, per instruction. Nothing below supports a conclusion; it exists to show
the machinery is genuinely prospective and to name the disagreement classes.

`model_version` **v0-72378bce8086** · cohort **BTC15m** · fitted on 693,916
canonical fills / 5,546 windows / 645 conditional states.

## Chronological separation is structural, not promised

v0 is fitted **only** on the canonical population, which **ends 2026-08-31**.
Every prospective prediction is scored on live data from **2026-09-15**. The two
cannot overlap, so leakage is impossible by construction rather than by
discipline.

## Preregistered horizons

**1 s / 5 s / 15 s / 30 s**, fixed in `src/l5_fidelity/model.py` before any score
existed. BTC15m's 2 s median inter-fill makes ACT/WAIT a different question at
each. They are not to be re-chosen after seeing which flatters the model.

## The four frozen predictions, and what L5 actually did

| id | frozen | state | P(ACT 30s) | pred side | pred size | pred gap |
|---|---|---|---|---|---|---|
| `911ad1e7` | 01:15:23 | `tte>420\|gap10-30\|long_up\|last=Up\|px40-60` n=3949 | 0.894 | Up (.503) | 15.1 | 5 s |
| `603e479b` | 01:16:08 | `tte>420\|gap1-3\|long_up\|last=Up\|px40-60` n=3550 | 0.946 | Up (.610) | 14.0 | 2 s |
| `7d1151b3` | 01:16:54 | `tte>420\|gap10-30\|long_up\|last=Up\|px60-80` n=3068 | 0.886 | Up (.541) | 16.2 | 5 s |
| `a2f225e3` | 01:17:40 | `tte>420\|gap10-30\|long_up\|last=Down\|px20-40` n=1997 | 0.920 | Down (.387) | 13.0 | 3 s |

| id | ACT≤30s | actual gap | actual side | actual size | side | size err | timing err | fee-bearing |
|---|---|---|---|---|---|---|---|---|
| `911ad1e7` | **yes** | 3.69 s | Down | **80.0** | MISS | **+64.9** | −1.3 s | False |
| `603e479b` | **no** | 31.08 s | Up | 30.0 | HIT | +16.0 | **+29.1 s** | **True** |
| `7d1151b3` | **yes** | 6.43 s | Down | 14.7 | MISS | −1.5 | +1.4 s | False |
| `a2f225e3` | **yes** | 23.81 s | Up | 5.0 | MISS | −8.0 | **+20.8 s** | False |

Store integrity: **4 frozen, 4 resolved, 0 tampered.** A second resolution
attempt on an already-resolved prediction was rejected (first writer wins), and
the seal is a content hash re-verified on load, so a silent edit is detectable
rather than merely forbidden.

## Stage A — ACT/WAIT (n=4, EARLY)

Actual ACT≤30 s: 3 of 4. Brier at the 30 s horizon: 0.011, **0.895**, 0.013,
0.006. The single large term is the one window where L5 went quiet for 31 s
while the model gave 0.946.

Against baselines at 30 s: `always_ACT` scores 1/4 wrong, v0 scores 1/4 wrong —
**v0 has not yet demonstrated any edge over the trivial predictor.** At n=4 that
is the only honest statement.

## The three largest disagreement classes

**1. Side prediction is at or below chance, and the model knows it.**
1 of 4 correct. The `p_outcome_up` values are 0.503, 0.610, 0.541, 0.387 —
barely distinguishable from a coin. The conditioning set (TTE, gap, residual
sign, last outcome, price bucket) carries almost no information about which side
L5 buys next. **This is the biggest gap and the first research target.**

**2. Size is predicted as a central value against a wildly dispersed truth.**
Actual clips: 80.0, 30.0, 14.7, 5.0 — a 16× range — against predictions of
13–16. The 80.0 clip is exactly the "repetitive clip size" the independent
review flagged, and v0 has no representation for it. A p50 is the wrong object;
size needs a distribution or a mixture.

**3. Timing is systematically under-predicted in the tail.**
Predicted gaps 2–5 s; actual 3.7, **31.1**, 6.4, **23.8**. Two of four are 4–10×
the prediction. The empirical gap distribution is heavy-tailed and the p50 point
estimate discards exactly the part that matters for ACT/WAIT.

## Execution economics, retained per fill

Observed `fee_bearing` after each fill: False, **True**, False, False. The
pre-fill observable state is stored with every prediction, so whether pre-fill
state predicts fee-bearing execution is testable prospectively as n grows. At
n=4 nothing is claimed — and note 1 of 4 sits oddly against the 1.1% recent
BTC15m rate, which is itself a reason to accumulate rather than interpret.

**Not labelled maker/taker anywhere.** `fee_bearing` is the observed cash wedge.

## Cohorts

BTC15m only. The other three are collected and will be fitted and reported
separately; none is pooled into BTC15m for sample size.

## Status

* **OBSERVATION** — canonical + recent populations, reconciled.
* **FIDELITY** — v0 freezing genuine prospective predictions now.
* **ADAPTED** — residual saturation cap20/35/50, unchanged, exact-L5 as control.

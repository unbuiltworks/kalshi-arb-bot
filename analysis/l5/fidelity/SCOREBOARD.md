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

---

# Update — n=17 resolved, and two real defects found

**EARLY / INSUFFICIENT SAMPLE (n=17).** Store: 19 frozen, 17 resolved, 0 tampered.

## Stage A — ACT/WAIT, model `v0-72378bce8086`

| horizon | actual ACT | mean P(ACT) | Brier | `always_ACT` Brier |
|---|---|---|---|---|
| 1 s | **0.000** | 0.363 | **0.135** | 1.000 |
| 5 s | **0.176** | 0.603 | **0.337** | 0.824 |
| 15 s | **0.294** | 0.809 | **0.489** | 0.706 |
| 30 s | **0.529** | 0.911 | **0.394** | 0.471 |

v0 beats `always_ACT` at every horizon — but it is **badly over-confident at
every horizon**, by 0.36 to 0.51 in probability.

### That over-confidence is my sampling design, not (necessarily) L5

The canonical hazard is fitted on **gaps between consecutive fills** — i.e.
measured from a fill instant. The accumulator freezes predictions at arbitrary
wall-clock moments, roughly every 20 s, which land **mid-gap**. For a
heavy-tailed gap distribution the residual waiting time from a random moment is
*longer* than a fresh gap: length-biased sampling, the inspection paradox.

So Stage A is currently measuring a fresh-gap model against residual-wait
observations. **The number is real; the interpretation "L5 is slower than we
thought" is not available from it.** Stage D agrees: timing error median
**+15.31 s**, i.e. actual gaps run consistently longer than predicted.

Two admissible fixes, neither applied yet: freeze only at fill instants, or
condition the hazard on elapsed-since-last-fill. The second requires putting
`since` back — as a *serve-time* conditioner on the timing table, which is where
the completed gap is the correct target, and not in the state key.

## Stage B — SIDE (n=11 with a fill)

| predictor | correct |
|---|---|
| **v0** | 6/11 = **54.5%** |
| baseline `repeat_previous_outcome` | 6/11 = **54.5%** |
| baseline `CHEAPER` | 4/11 = 36.4% |

**v0 does not beat the trivial persistence baseline.** At n=11 that is the only
statement available — but the canonical analysis below says why.

## Stage C / D

Size error median **+29.00**, mean |error| 34.72 (n=11) — actual clips run far
larger than the predicted p50. Timing error median **+15.31 s** (n=11).

## Execution economics

Observed fee-bearing 1/11 = 9.1%, against the 1.1% recent BTC15m rate. n=11;
nothing claimed. Pre-fill state is stored with every prediction, so the
"does pre-fill state predict fee-bearing execution" test accumulates.

---

# The train/serve skew — a real bug, found and fixed (v0 → v0.1)

On 206,512 chronologically OOS BTC15m consecutive-fill pairs:

| feature | OOS AUC for "next fill is Up" | |
|---|---|---|
| **last outcome** | **0.7359** | **USABLE** |
| **up price** | **0.3955** (0.605 inverted) | **USABLE** |
| residual (relative) | 0.5063 | NO SIGNAL |
| TTE | 0.4928 | NO SIGNAL |
| fills so far | 0.5107 | NO SIGNAL |
| run length | 0.4950 | NO SIGNAL |

P(next=Up | last=Up) = **0.733**; P(next=Up | last=Down) = **0.261**.
P(next=Up) falls monotonically with the up price: **0.659** at 0.0–0.1 down to
**0.339** at 0.9–1.0 — he buys the cheap side.

**v0 was emitting p_up ≈ 0.50 in exactly those states.** The cause:
`fit` built the key with `last_outcome = s[i-1]` — the fill *before* the current
one — while the runner serves `last_outcome` = the *most recent* fill, `s[i]`.
Trained on one meaning, queried with another, and the skew destroyed the single
strongest feature in the dataset.

Also removed from the key: `since`. At fit only the **completed** gap is known;
at serve only the **elapsed-so-far**. Keying on it trained against a quantity
that cannot be observed at prediction time.

**v0.1-e9983c17ff8c** now emits p_up **0.700** for `last=Up` and **0.280** for
`last=Down`, matching the data's 0.733 / 0.261. Per the versioning rule, v0.1
scores **prospectively from here**; it takes no credit for v0's 17 predictions,
which remain in the store scored as v0.

## Revised largest disagreement classes

1. **ACT/WAIT calibration** — now understood as length-biased sampling, fixable
   in the harness rather than the model.
2. **Size** — a p50 against a 16× dispersion; needs a distribution, not a point.
3. **Side** — was the biggest; the mechanism is now identified and fixed, and
   v0.1 needs prospective evidence before any claim is made for it.

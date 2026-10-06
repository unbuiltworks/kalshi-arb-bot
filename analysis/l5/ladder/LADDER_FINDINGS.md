# Resting-ladder, replenishment/range and strike-distance — results

Frozen protocol **f07aec7c**. 2,795 BTC15m L5 **maker** fill records, 12
windows. **Bids only** — the crossed-book defect is ask-side. S3 untouched
and frozen at `8b20a259…87df`. No live orders. No model selected by
settlement P&L.

## Headline

**Most of what we have been calling L5 "sizing" is counterparty-determined
partial filling: 91.0 % of irregular fill quantities by count and 88.3 % by
volume are parsimoniously explained as partial consumption of larger latent
clips.** The ladder is real, concurrent and **touch-following** — but the
*replenishment-cycle* model of it is **refuted**, and strike-sensitivity is
**unidentifiable**.

## 0. STRIKE TEST — NOT IDENTIFIABLE, and not run

The parquet schema carries **no strike, no reference price, no settlement
index**. Coinbase was not silently substituted.

The frozen gate **G-STRIKE** tested the one structural opening — that these
markets resolve on close vs their own open, which would make
distance-from-strike a *difference* in which a proxy's level bias cancels:

| | |
| --- | --- |
| agreement, `sign(proxy_close − proxy_open)` vs archive `winner` | **10 / 12** |
| frozen bar | **≥ 11 / 12** |
| **verdict** | **FAIL** |

Per protocol, **the entire strike-distance analysis was not run.**

Worth recording without reclassifying: the two mismatches are the two
*smallest* proxy moves (**−0.044 %** and **−0.015 %**) — the proxy cannot
resolve the sign inside the venue basis. The mechanism may well be right;
the proxy simply cannot establish it. That is precisely why the gate
existed, and the exact-strike question stays **UNRESOLVED**.

## 1. Q1 — irregular sizes are partial consumption

| | count | share volume |
| --- | --- | --- |
| total maker fills | 2,795 | 65,204 shares |
| **irregular** quantities | 2,072 | 43,220 shares |
| **irregular explained as partial consumption** | **90.97 %** | **88.29 %** |
| all fills explained | 91.56 % | 88.53 % |
| unexplained singleton clips | **21** | — |

Reported separately as required, never pooled. Only **21** singleton
irregular fills lack clip context and count against the hypothesis.

## 2. Q2 — minimum ladder width, a LOWER BOUND

| max concurrent levels (lower bound) | |
| --- | --- |
| median | **7** |
| IQR | 5 – 11 |
| range | **5 – 15** |

Computed by **interval overlap**: a level filled before second *t* and
again after *t* must have been live across *t*, unless a cancel-and-repost
occurred — which the archive cannot evidence.

*(Corrected during the run: my first version counted every level filled at
or after each second, which at t = 0 is just the window's total distinct
level count and is not a simultaneity bound at all. It reported a median of
40.)*

**This is a floor, never observed resting orders.** Cancels and unfilled
orders are invisible. It is nonetheless well above the "≥3 simultaneous
levels" floor from the earlier geometry work.

## 3. Q3 — concurrency

| | |
| --- | --- |
| qualifying `A → B → A` returns | **109** |
| windows with ≥1 qualifying return | **12 / 12** |
| `INDETERMINATE_SAME_SECOND` (not counted either way) | 144 |

A return to a price with **no evidenced replenishment in between**, across
≥2 distinct seconds, is far more naturally explained by a level that was
resting all along than by cancel-and-repost. Present in **every** window.

144 same-second pairs are reported as indeterminate and claimed for
neither model — one-second block resolution cannot separate concurrency
from fast reposting.

## 4. Q4 — the 0.06 bound is a FILL-LOCATION STATISTIC

| | observed | frozen bar for "width feature" |
| --- | --- | --- |
| deepest-offset IQR | **0.05** | ≤ 0.02 |
| rho(touch travel, deepest offset) | **+0.44** | \|rho\| < 0.30 |

**Both criteria fail → `FILL_LOCATION_STATISTIC`.** The deepest inferred
level scales with how far the touch travelled, which is the signature of a
fill-location statistic, not a fixed ladder depth.

| | median | IQR | max |
| --- | --- | --- | --- |
| deepest offset from touch | **$0.09** | 0.07 – 0.12 | $0.44 |
| ladder width (best → deepest level) | **$0.63** | 0.54 – 0.77 | $0.95 |

The ~0.06 displacement bound — derived as the p99 of *fill offsets* — is
**not** a ladder-width feature. The actual inferred span is an order of
magnitude wider.

## 5. REPLENISHMENT — the primary alternative model is REFUTED

**R1 / R2 — lattice clustering FAILS.**

| | observed on lattice | worst baseline | excess | bar 3× |
| --- | --- | --- | --- | --- |
| **R1** forced-80 | 13.33 % | 5.73 % (empirical resample) | **2.33×** | **FAIL** |
| **R2** empirical partial clips | 13.33 % | 7.16 % | **1.86×** | **FAIL** |

Per-level cumulative consumption does **not** cluster near 80 / 160 / 240 /
320 strongly enough against appropriate baselines. The
`0 → 80 → replenish → 80 → …` depletion-cycle picture is **not supported**.
Note R2 is *weaker* than R1 — allowing the empirical partial-clip
distribution makes the clustering less distinctive, so any apparent
clustering is partly an artifact of forcing clips to start at 80.

**R3 — what actually happens after depletion:**

| operation | n |
| --- | --- |
| **EXTEND_LADDER** (deeper level activates) | **224** |
| **RECENTER** (old level gone, exposure nearer the moved market) | **89** |
| **REPLENISH** (same price reappears) | **16** |
| **WITHDRAW** (no replacement) | 13 |

**Replenishment at the same price is rare — 16 of 342 events (4.7 %).** The
dominant response is to activate a *deeper* level. This is the direct
refutation of the replenishment model: L5 does not refill a depleted price,
he is filled further down.

**Confound stated:** `EXTEND_LADDER` vs `RECENTER` is defined by the
direction of the next level, so in a trending market the next level is
systematically lower and the taxonomy is partly reading the price path.
The REPLENISH count (same price, direction-free) is **not** subject to this
confound, and it is the one that refutes the model.

Latency to the next attributable fill: median **3 s**, IQR 0–13 s, max
135 s — reported **explicitly as an upper bound on replacement latency**,
since unfilled placement is unobservable.

**R4 — operations vs fills FAILS:** ratio 0.59–0.65 against a 0.5 bar,
**0 / 12** windows meeting it. The four operations do compress the
description by ~40 %, which is real, but short of the preregistered bar.

## 6. ENVELOPE — touch-following, not a fixed price range

LOWO median L1 error on envelope **center**:

| model | L1 | held-out wins |
| --- | --- | --- |
| **E2** fixed distance around touch ≡ probability-following | **0.0396** | 7 |
| **E3** inventory-dependent | **0.0395** | 5 |
| **E1** fixed absolute contract-price range (nests fixed-around-0.50) | **0.2042** | 0 |

**E2/E3 beat E1 by 5×, winning 12/12 held-out windows between them.** The
envelope **follows the touch**; it is not a fixed absolute price band.

**E2 vs E3 is `NOT_DISCRIMINATED`** — 0.18 % apart, 7/5 split. Inventory
dependence adds essentially nothing.

Per the degeneracy frozen before results, **E2's win is ONE result, not
two**: on a prediction market the contract price *is* the probability, so
"fixed distance around touch" and "probability-following/recentering" are
the same model, and "fixed distance around 0.50" is nested in E1.

Envelope width: median **$0.07**, IQR $0.04–0.13 (n = 3,232).

*(Corrected during the run: E3 was initially not implemented at all — the
code multiplied the inventory slope by literal `0.0`, making E3 identical
to E2 and producing a spurious NOT_DISCRIMINATED between them. E3 now fits
a real least-squares inventory coefficient. The E1 comparison was
unaffected.)*

## 7. Pre-positioned vs reactive

| | |
| --- | --- |
| share of levels with first-fill age ≥ 30 s | **76.1 %** (bar ≥20 %) |
| median level age at first fill | 198 s |
| windows with qualifying `A → B → A` | **12 / 12** (bar ≥9) |
| **PRE_POSITIONED bar** | **MET** |
| **REACTIVE bar** | not met |
| **verdict** | **PRE_POSITIONED** |

**Two limitations, both disclosed rather than smoothed over:**

1. **The level-age metric is weaker than the protocol assumed.** Once a
   level is reachable, a pre-positioned order fills immediately (age ≈ 0)
   and a reactive repost fills after a short lag (age small) — *both*
   produce short ages, so age does not cleanly separate the models. The
   large observed ages instead reflect levels first reached early and
   filled on a later visit. **The `A → B → A` test carries the
   discriminating weight.** Reported as frozen, not re-specified.
2. **7.6 % of ages are negative** — a fill recorded before the second in
   which `best_bid ≤ price`, an artifact of one-second event-driven
   snapshots.

## Evidence tiering

### DIRECTLY OBSERVED
- **91.0 % of irregular fill quantities (88.3 % by volume) sit inside
  multi-fill latent clips**; only 21 unexplained singletons.
- **109 `A → B → A` level returns across 12/12 windows** with no evidenced
  replenishment in between.
- **Same-price replenishment is rare: 16 of 342 post-depletion events.**
- **The envelope tracks the touch**: E2/E3 beat a fixed absolute range by
  5×, 12/12 held-out windows.
- Deepest offset median **$0.09**, ladder span median **$0.63**, envelope
  width median **$0.07**.
- The archive carries **no strike, no reference price, no index**.

### STRONGLY INFERRED
- **At least 5–15 price levels are concurrently live** (median 7), as an
  interval-overlap lower bound.
- **L5 does not choose observed fill quantities**; counterparties do. The
  quantity distribution is a consumption artifact.
- **The ~0.06 displacement bound is a fill-location statistic**, not a
  ladder-width parameter.
- **Liquidity is pre-positioned rather than reactively reposted**, resting
  on the A→B→A evidence rather than on level age.

### POSSIBLE
- Post-depletion behaviour is dominated by deeper-level activation rather
  than same-price refill — but the EXTEND/RECENTER split is confounded by
  price drift.
- Inventory may modulate envelope centre; E3 edges E2 by 0.18 %, far short
  of discriminating.
- The strike mechanism may be "close vs own open" — the two G-STRIKE
  mismatches are the two smallest moves — but this is **not established**.

### UNIDENTIFIABLE
- **The exact settlement index and any absolute strike distance.** G-STRIKE
  failed; no strike analysis was run.
- **Actual resting order sizes and the true ladder width.** Cancels and
  unfilled orders leave no trace; every count here is a floor.
- **Concurrency within a single second** — 144 indeterminate pairs.
- **Replacement latency.** Only an upper bound (fill latency) is observable.
- Whether ladder activation follows strike distance more consistently than
  contract probability — untestable here, and collinear by construction
  even with a valid index.

## Answer to the question asked

**How much of "L5 sizing" is counterparty-determined partial filling?**
**Roughly 90 % — 91.0 % of irregular quantities by count, 88.3 % by
volume.** Treating individual fill sizes as decisions is not supportable.

**How much evidence is there for a pre-positioned multi-level two-sided
ladder?** Strong for **multi-level concurrency** (≥5–15 levels, A→B→A in
every window) and for a **touch-following envelope** of median $0.07 width
within a $0.63 span. The **replenishment-cycle** version of the model is
**refuted** — the 80-lattice clustering fails its baselines (2.33× against
a 3× bar), same-price refill occurs in under 5 % of depletion events, and
the four operations miss the compression bar in 0/12 windows.

**Around strike-sensitive probability movement?** The probability half is
supported — the envelope follows the touch, which *is* the probability. The
**strike half is unidentifiable** and was not tested.

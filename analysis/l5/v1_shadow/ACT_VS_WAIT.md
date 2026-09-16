# V1 ACT vs WAIT: the model cannot say WAIT, and we are asking it to

Analysis only. **Nothing in the controller, k, the pair target, the thresholds,
the model or Envelope A was changed.** The V1 shadow is running unchanged.

## Sample

27 rolling summaries × 5,000 rows, 08:00Z → 12:24Z on 2026-09-16 (~49k
observation rows total, ~46 sports markets per cycle), from the deployed
shadow `17d027dd` / commit `76061d06`.

| measure | mean | min | max |
|---|---|---|---|
| **PROPOSE rate** | **0.7949** | 0.7658 | 0.8352 |
| WAIT rate | 0.2051 | — | — |
| full-state coverage | **0.0325** | 0.0206 | 0.0476 |
| state-key lift changed the answer | **0.981** | — | — |

Relations proposed: `extend` ~65%, `complete` ~33%, `open` ~2%.

## Decomposition, as requested

**By match tier.** The model has 988 full states, 136 base states, 12 marginal
entries; `v2_dist` shrinks full → base → marginal.

| tier | share of rows | what it means |
|---|---|---|
| exact / full-state match | **3.3%** | V1 is in a state L5 was observed in *immediately before trading* |
| base fallback | **96.7%** | price/tte/imbalance cell has L5 evidence, but not with this last-action context |
| marginal fallback | **~0%** | `base` covers 136/140 = 97.1% of the space, so the generic prior is almost never reached |

That last row corrects a natural assumption: the backoff is **not** landing on
a generic global prior. It lands on `base`, which is real L5 evidence for that
price/tte/imbalance cell. The actions being proposed are L5's own marginal
behaviour in that cell — just without the context of what we last did.

**States observed immediately before an L5 trade vs states without
contemporaneous L5 activity.** These are the same split: the `full` keys *are*
"L5 was here and then acted", because the corpus is built from L5's fills. So
**96.7% of V1's proposals are made from states with no contemporaneous L5
trading evidence at that context**.

**By divergence class.** Across all 27 summaries:

- `NONE` ≈ 79.5%
- `RISK_CAP_FORCED` ≈ 20.5% — `residual_ceiling` almost all of it, with
  `exposure_ceiling` appearing from 11:33Z as theoretical inventory accumulated
- `MODEL_DISAGREEMENT` — **zero, ever**
- `VENUE_QUANTIZATION_FORCED` — **zero**; `pinned_to_venue_minimum` ≈ 0.1% and
  never forces a WAIT

**Every single WAIT in the entire sample is a risk cap. The model has never
once declined to act.**

## Which of the four causes

### 1. Implementation / WAIT-semantics bug — PARTLY, but not the one suspected

V1's WAIT semantics are correct. It waits on `model_unavailable`, on the
residual and exposure ceilings, and on no-size-after-scaling, and it attributes
each correctly. That hypothesis is refuted by the data.

**But there is a real implementation defect next to it.**
`fidelity_decision()` takes `last_rel` and `last_size` with defaults
`("open", "q<5")`, and **`observe()` never passes them** — the only occurrences
in `src/l5_v1_shadow.py` are the signature and the `v2_dist` call. So every
lookup, on every row, uses the default context.

```
full keys reachable with the frozen default : 40 of 988      (4.0%)
L5 transitions reachable                    : 202 of 69,646  (0.29%)
```

**The shadow is scoring against 0.29% of L5's observed transition evidence.**
That is what caps full-state coverage at ~3.3%, and it means the fidelity
comparison is currently much weaker than it looks.

It changes *which* action is predicted. It does **not** cause over-action —
see below.

### 2. Shrinkage / backoff making generic states actionable — CONTRIBUTING, milder than feared

96.7% of rows are backed off, so shrinkage supplies the answer almost always.
But it backs off to `base`, not to the marginal, and `base` is genuine L5
evidence. More importantly, backoff cannot be the root cause: **a perfect
full-state match would also always produce an action**, because no state in
`full` contains a no-action outcome.

### 3. Missing historical NO-ACTION training states — **ROOT CAUSE**

```
relation vocabulary in `full` : {open, complete, extend}
marginal (12 entries)         : complete|*, extend|*, open|*   — nothing else
tokens wait/hold/none/noop/no_action/idle/skip/pass
  anywhere in the artifact    : ABSENT, all of them
formulation                   : "v1 base state + last_rel + last_size,
                                 shrinkage alpha=10.0 (full->base->marginal)"
n_transitions                 : 69,646
```

The artifact is a **transition model over L5's fills**, keyed on having just
acted. It answers *"given L5 acted, what did it do?"* — and V1 is asking it
*"should I act?"*, which it structurally cannot answer. **`P(act) = 1` by
construction.**

So the observed ~79.5% proposal rate is exactly `1 − P(risk cap binds)`. It is
not a bug and not a disagreement; it is the model's domain.

**Waiting is in the artifact — as timing, not as an action.** `cadence_modal`
carries a per-state modal inter-action gap (`gap<2s`, `gap2-10`, `gap10-30`,
`gap>30`). L5's selectivity is expressed as *how long it waits between
actions*, and V1 never consults it. That is where a WAIT semantics would come
from, and it needs no new data collection.

### 4. Genuine Polymarket-US-sports transfer disagreement — NOT YET MEASURABLE

Cannot be assessed while the model cannot express NO-ACTION and while the
context defect restricts lookups to 0.29% of the evidence. Any apparent
disagreement today is confounded by (3) and (1). Claiming a transfer result now
would be measuring our own construction.

## Counterfactual: exact / full-state-only matching

**Analysis only — the controller is unchanged.**

If a full-state miss were treated as WAIT:

```
PROPOSE rate ≈ 0.0325 × 0.795 ≈ 0.026  →  ~2.6%   (vs 79.5% today)
                                           a ~31× reduction
```

This is the product of two measured marginals; the joint is not in the logs, so
it is an estimate under independence, not a measured figure.

Two caveats that matter more than the number:

- With the context defect present, "exact-only" means *exact within the 4% of
  the model we can currently reach*. Fixing `last_rel`/`last_size` first would
  raise coverage substantially before any such gate were considered.
- Exact-only matching is a **proxy** for selectivity, not the mechanism. It
  gates on *evidence availability*, whereas L5's own selectivity is a *timing*
  behaviour. Gating on coverage would produce a quieter engine for the wrong
  reason.

## Candidate live envelopes — unranked, unarmed

Derived from L5's **early** regime (2026-03-19 → 04-19), where the
continuous-inventory shape is already present: $128.69 median window notional
($836.96 worst), $32.90 median peak residual ($145.79 worst), 28 fills/window,
10-share typical fill.

`k(worst)` is the binding scale against L5-early's **worst** window —
the conservative reading. Venue grid: min 0.01, step 0.01.

| | residual | exposure | concurrent | daily stop | k (typical) | k (worst) | 10sh → qty | distortion | fills/day |
|---|---|---|---|---|---|---|---|---|---|
| **A** frequency-first | $3.00 | $30.00 | 3 | $6.00 | 0.0777 | **0.0119** | 0.1195 → 0.12 | 0.4% | ~336 |
| **B** balanced | $8.00 | $75.00 | 6 | $15.00 | 0.0971 | **0.0149** | 0.1494 → 0.15 | 0.4% | ~672 |
| **C** early-L5 shaped | $20.00 | $200.00 | 10 | $40.00 | 0.1554 | **0.0239** | 0.2390 → 0.24 | 0.4% | ~1120 |

All three sit **above** the k ≥ 0.006 shape floor (1.9×, 2.4×, 3.9× the
deployed k = 0.00619) and none pins to the venue minimum at a typical fill;
quantization distortion is ≤0.4% at both the typical and p90 fill size, so
trajectory shape is preserved in all three.

They vary **concurrency and repetition first** and order size only as a
consequence, per the frequency-and-breadth hypothesis. $30 appears in candidate
A only because it is the smallest of the three, not because Envelope A used it.

**Not ranked and not recommended — the owner chooses the live authorization.**

## What this does and does not establish

- Establishes: V1's action rate is fully explained by the model's vocabulary;
  the risk caps work and are the only thing producing WAIT; the state-key lift
  matters on 98% of rows; there is a specific, small context defect costing
  99.71% of the evidence.
- Does not establish: whether L5's policy transfers to Polymarket US sports.
  That question is unanswerable until the model can express NO-ACTION.

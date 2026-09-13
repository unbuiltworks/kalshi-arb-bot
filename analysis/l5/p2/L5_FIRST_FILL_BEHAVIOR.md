# P2 — what does L5 respond to that our clone cannot see?

**Verdict: A2 is PARTIALLY supported, and A4 applies to the rest.** Specific frozen keys do collapse materially different L5 behaviour — gaps up to **24.7 percentage points** — but no omitted variable materially improves out-of-sample prediction in aggregate.

Corpus: canonical **2,216 windows / 69,559 decision points** (Mar 19 – Aug 31) and fresh **4,220 windows / 136,431 points** (Sep 1–10). Reconstruction reconciles **3,510/3,510** windows against L5's own venue redemptions. Split by **time**, never at random. Nothing deployed.

---

## Data-access task — answered, and the answer is no

No safe existing read-only path to the durable probe ledger exists:

| candidate | result |
|---|---|
| Railway volume file read | **no such capability** in the plugin surface |
| Railway CLI (`railway ssh`) | **not installed** in this environment |
| HTTP endpoint on the probe | **none** — the probe has no server; it only *reads* the recorder's |
| scheduled ops export | **none** found |
| local copy / artifact | **none** — checked `data/`, `analysis/` |

Per instruction I did **not** modify production to make analysis convenient. P2 is unaffected: it runs on L5's canonical history, which is local.

## The headline fact

**L5 continues the SAME side on his next fill 68.9% of the time (canonical) and 71.0% (fresh).** He is strongly side-persistent. Our own *acted* distribution is complete 50.7% / extend 20.3% — we complete roughly 2.5× more often than we extend, while L5 does the opposite.

Note the fidelity gate already shows our **sampled** distribution matching L5 almost exactly (L1 0.0048) while **acted** diverges (L1 0.4689). The model reproduces him; the safety shell then converts extends into completes and holds.

## P2-B — does an omitted variable predict better? Mostly no.

Out-of-sample (70/30 by time), predicting "is L5's next fill the same side":

| variable | canonical ΔLogloss | fresh ΔLogloss | ΔAccuracy (both) |
|---|---|---|---|
| **side_streak** | **+0.68%** | **+1.59%** | ~0 |
| recent_price_move | −0.03% | +0.21% | ~0 |
| effective_pair_cost | +0.08% | +0.15% | ~0 |
| imbalance_ratio | +0.21% | +0.03% | ~0 |
| matched_qty | +0.09% | −0.04% | ~0 |
| abs_spend | −0.03% | −0.09% | ~0 |
| held_avg_cost | **−0.16%** | +0.00% | ~0 |
| implied_headroom_bound | **−0.16%** | −0.02% | ~0 |

Baselines: canonical logloss 0.60423 / acc 0.6974; fresh 0.55730 / acc 0.7575.

**Accuracy does not move for any variable.** `held_avg_cost` and the headroom bound make canonical *worse*. `side_streak` is the only consistent improvement in both eras, and it is small.

## But the within-key splits are real and large

Same frozen key, split at the median of one omitted variable — L5's same-side rate on each half:

| frozen key | variable | n | low | high | **gap** |
|---|---|---|---|---|---|
| `p10-25c\|tte2-5m\|imb1-10` (fresh) | side_streak | 231 | 0.664 | 0.911 | **0.247** |
| `p<10c\|tte2-5m\|imb10-50` | side_streak | 379 | 0.654 | 0.883 | **0.229** |
| `p10-25c\|tte30-120s\|imb10-50` | effective_pair_cost | 230 | 0.500 | 0.728 | **0.228** |
| `p<10c\|tte2-5m\|imb10-50` | matched_qty | 379 | 0.632 | 0.852 | 0.220 |
| `p<10c\|tte30-120s\|imb10-50` | abs_spend | 362 | 0.593 | 0.806 | 0.212 |
| `p10-25c\|tte30-120s\|imb10-50` (fresh) | recent_price_move | 468 | 0.612 | 0.805 | 0.193 |

**These are exactly what you asked for: situations our clone cannot tell apart where L5 demonstrably does different things.**

### Why large splits yet no accuracy gain — and why it still matters

A cell splitting 0.65 / 0.88 still predicts "same side" for *both* halves, so argmax accuracy is unchanged. Only calibration moves, which is why logloss improves slightly and accuracy does not.

**That distinction would be fatal for a deterministic policy and is not for ours.** Our engine *samples* from the distribution. A cell that should be 0.88 but is modelled at 0.70 produces materially the wrong action frequency every time it is visited. So the splits are behaviourally meaningful even though they are predictively marginal.

## P2-C — fresh L5 sequence, against your five questions

Using the complete Sep 1–10 feed (144,394 rows), not the screenshot:

1. **Enters one side repeatedly?** **Yes** — 71.0% same-side continuation, and `side_streak` is the strongest discriminator in both eras. Streak-persistence is his most identifiable habit.
2. **Alternates as relative value changes?** **Partially** — `effective_pair_cost` splits one key 0.500/0.728, so pair economics do move him, but weakly overall.
3. **Waits for specific price movement?** **Weak evidence** — `recent_price_move` is the #2 fresh variable (+0.21%) and splits one key by 19pp, but adds nothing on canonical.
4. **Changes as absolute inventory grows?** **No material effect** — `abs_inventory`, `abs_spend`, `matched_qty` all ≈0 or negative out-of-sample.
5. **Changes as settlement approaches?** TTE is already *in* the frozen key, so this is not an omission — it is represented.

## What public data cannot establish — labelled, not inferred

- **maker/taker: UNKNOWN.** No liquidity flag is published per fill.
- **Contemporaneous opposite price: UNKNOWN.** The feed publishes his fills, not the book. **True completion headroom is therefore not reconstructable for L5.** What I computed is an *implied bound* (1.00 − his held-side average cost) — what he *could* pay, not what was offered. It was the **weakest** variable tested, and I am not treating that as evidence about headroom behaviour.
- Order intentions, rests and cancels: **UNKNOWN**.

Incentives, account-level and unattributable (empty `conditionId`): MAKER_REBATE $11,113.45, TAKER_REBATE $1,147.02, REWARD $8,229.66 over Sep 1–10.

## Decision output

| option | verdict |
|---|---|
| **A1** — admission too permissive | **not tested here.** P2 measures L5, not our gate. P0-B's evidence (complement ~60 ticks away) still points this way. |
| **A2** — state key missing a variable | **PARTIALLY SUPPORTED.** `side_streak` is the consistent omission; specific keys split by up to 24.7pp. Aggregate predictive gain ≤1.6% logloss, ~0 accuracy. |
| **A3** — first fills resemble L5, difference is later | **SUPPORTED by the fidelity gate**, not by P2: sampled L1 0.0048 vs acted L1 0.4689. The divergence is the safety shell, not the model. |
| **A4** — insufficient evidence | applies to the headroom question specifically, which public data cannot answer. |

**Recommended next step, not taken:** build a shadow model keyed on `frozen_key × side_streak` and run it side-by-side against production. That is the one change the evidence supports, it is the smallest possible, and per your instruction it goes to **shadow first** with an action-by-action comparison before any live money.

**Not done:** the live-first-fill-vs-L5-state matching (P2 "most important comparison") needs our durable ledger for a usable n — 14 markets is too thin, and that data is the thing I cannot reach.

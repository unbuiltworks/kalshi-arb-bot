# ACT/WAIT: building the question V1 was never asking

Fidelity only. No P&L anywhere in this work, no threshold chosen to make
returns look better, nothing armed, no live order, no change to Gemini, no new
rung. The V1 shadow keeps running unchanged as the control.

## 1. The corrected sequential context — measured, not asserted

V1 called the v2 transition model with `last_rel`/`last_size` left at their
defaults on every row, so every lookup was `(open, q<5)`.

Held out on 10,714 opportunities (chronological split, no window on both sides):

| Stage-2 lookup tier | V1 frozen `(open, q<5)` | V1.1 real context |
|---|---|---|
| **full** | **0.0817** | **0.9990** |
| base | 0.9183 | 0.0010 |
| marginal | 0.0000 | 0.0000 |

**8.2% → 99.9%, a 12.2× improvement in exact-state coverage.**

And it is not merely coverage. Scoring ACTION | ACT on 20,931 held-out
transitions, where the majority-class baseline ("always extend") is 0.5139:

| | relation acc | side acc | size exact | size MAE (buckets) | full coverage |
|---|---|---|---|---|---|
| V1 frozen | 0.5457 | 0.5457 | 0.3428 | 0.973 | 0.043 |
| **V1.1 real context** | **0.6875** | **0.6875** | **0.3838** | **0.931** | **0.9998** |

Without the fix Stage 2 was **barely above majority-class** (0.5457 vs 0.5139) —
it was adding almost nothing. With it, +14 points of relation accuracy.

Confusion, V1.1 (truth → predicted): `extend` n=10,756 → 0.73 extend / 0.26
complete; `complete` n=10,175 → 0.64 complete / 0.35 extend. The residual error
is extend↔complete, which is the genuinely hard call.

Fixtures in `tests/test_v1_1_sequential_context.py` pin that OPEN → EXTEND →
COMPLETE yields three different lookup keys, that size is bucketed in **L5
share units** (a 0.06-contract action lifted by 1/k is `q5-15`, unlifted it is
wrongly `q<5`), and that `V1.observe`'s defaults are unchanged so the running
control is not altered.

## 2. The historical ACT/WAIT dataset

Source: `analysis/l5/l5_canonical_sequence.csv.gz` — 71,984 L5 fills in 2,216
reconstructed windows, carrying `seconds_to_settlement`, price, cumulative
up/down, matched and signed residual.

**Available at T, and therefore used:** last price L5 traded (carried forward —
exactly the `last_price` the deployed `state_key` already consumes), seconds to
settlement, carried-forward inventory/matched/residual, `last_rel`/`last_size`,
elapsed since last action, action index.

**NOT available, and not manufactured:** the order book at T (no bid, ask,
spread or depth — we hold L5's fill tape, not a market tape); any other
trader's activity; whether a resting L5 quote was filled, cancelled or
repriced; maker/taker on 5,269 of the 71,984 rows, which the corpus marks
`unknown`.

**Opportunity sampling.** A grid of `interval_s` inside each window, starting
after the first fill. No special-casing of pre-fill instants, so the base rate
falls out of the data rather than being set by how many positives we chose to
add. Label = did L5 act within H.

| interval | opportunities | H=2s | H=10s | H=30s | H=120s |
|---|---|---|---|---|---|
| 5s | 109,235 | 0.1547 | 0.4672 | 0.7369 | 0.9581 |
| **15s** | **35,828** | **0.1285** | **0.4637** | **0.7403** | **0.9573** |
| 30s | 17,445 | 0.1327 | 0.4707 | 0.7467 | 0.9580 |
| 60s | 8,287 | 0.1428 | 0.4902 | 0.7571 | 0.9598 |

Base rates are stable across a 12× change in spacing, so nothing below is an
artefact of the grid. At 15s: 1,818 windows, span 2026-03-19 → 2026-08-31,
next-gap p10/p50/p90 = 2 / 12 / 70 s.

### The scope limit, stated plainly

Every negative is sampled from inside a window L5 is **already trading**, so
this estimates

> `P(L5 acts within H | L5 is active in this market)`

and **not** `P(L5 enters a market it is not in)`. We cannot know which markets
L5 looked at and declined — we only see where it traded. This is where the
decision volume is (V1's proposals are ~65% extend, ~33% complete, ~2% open, so
it covers ~98% of V1's decisions), but the OPEN/entry decision is **out of
scope** until a collector records the candidate universe. See §5.

The data confirms the limit empirically: by `last_rel`, PR-AUC is 0.72 for
`extend` and `complete` and **0.21 for `open`**.

## 3. Chronological OOS results vs baselines

Split by time, 70/30, pushed forward so no window straddles the cut —
train 25,114 rows / 1,192 windows (Mar 19 → Aug 30), test 10,714 / 626 windows
(Aug 30 → Aug 31), **window overlap 0**. Threshold chosen on train only, by F1.

| H | model | base | pred | prec | rec | **PR-AUC** | falseACT | missACT | calErr |
|---|---|---|---|---|---|---|---|---|---|
| **2s** | **staged** | 0.143 | 0.248 | 0.266 | 0.462 | **0.2565** | 0.182 | 0.077 | 0.023 |
| | always-ACT | | 1.000 | 0.143 | 1.000 | 0.1428 | 0.857 | 0.000 | 0.857 |
| | base-rate | | 1.000 | 0.143 | 1.000 | 0.1428 | 0.857 | 0.000 | 0.020 |
| | cadence-only | | 0.196 | 0.282 | 0.388 | 0.2344 | 0.141 | 0.087 | 0.016 |
| | exact-state-only | | 0.279 | 0.255 | 0.497 | 0.2290 | 0.208 | 0.072 | 0.055 |
| **10s** | **staged** | 0.495 | 0.676 | 0.623 | 0.851 | **0.7088** | 0.255 | 0.074 | 0.034 |
| | always-ACT | | 1.000 | 0.495 | 1.000 | 0.4951 | 0.505 | 0.000 | 0.505 |
| | cadence-only | | 0.740 | 0.585 | 0.875 | 0.6532 | 0.307 | 0.062 | 0.034 |
| | exact-state-only | | 0.642 | 0.622 | 0.805 | 0.6488 | 0.243 | 0.096 | 0.092 |
| **30s** | **staged** | 0.764 | 0.848 | 0.828 | 0.919 | **0.9220** | 0.146 | 0.062 | 0.028 |
| | always-ACT | | 1.000 | 0.764 | 1.000 | 0.7640 | 0.236 | 0.000 | 0.236 |
| | cadence-only | | 1.000 | 0.764 | 1.000 | 0.8653 | 0.236 | 0.000 | 0.021 |
| **120s** | **staged** | 0.961 | 0.998 | 0.962 | 0.999 | **0.9910** | 0.038 | 0.001 | 0.006 |
| | always-ACT | | 1.000 | 0.961 | 1.000 | 0.9614 | 0.039 | 0.000 | 0.039 |

Sampling-interval sensitivity of the headline (H=10s PR-AUC): **0.7196 / 0.7088
/ 0.7106 / 0.7186** at 5/15/30/60s. The result does not depend on the grid.

### What this says

- **The staged model beats every baseline at every horizon.** At H=10s it
  roughly halves the false-action rate versus always-ACT (0.255 vs 0.505) while
  keeping recall at 0.851.
- **The value is at SHORT horizons.** Inside an active window L5 acts within
  120s **96%** of the time, so at H=120s "always ACT" is already nearly right
  and the model adds little. At H=2s the base rate is 0.143 and the model
  nearly doubles PR-AUC. **So the useful question is not "does L5 act" but
  "does L5 act *now*"** — which is a timing question.
- **Cadence alone carries most of the signal** (0.6532 of the staged 0.7088 at
  H=10s). Elapsed-since-last-action is the dominant feature; price, TTE and
  inventory add a real but secondary increment.
- **Exact-state-only is not the answer.** It scores 0.6488 at H=10s, below both
  the staged model and cadence-only, and is the worst-calibrated of the three
  (0.092). Gating on evidence availability is the wrong mechanism, as suspected.
- **Slices** (H=10s): PR-AUC by TTE 0.61 (tte2-5m) → 0.76 (tte5-10m); by price
  0.63 (p55-75c) → 0.77 (p<25c); by last_rel 0.72 extend / 0.72 complete /
  **0.21 open**.

## 4. Timing / cadence

Stage 3 predicts the next-action gap bucket on the artifact's own boundaries.

```
bucket accuracy            0.4867
modal-baseline accuracy    0.4001      lift +0.0865
median |gap error|         10 s        p90 52 s
lookup tiers               full 9,123 / base 1,585 / marginal 6
```

Confusion (truth → predicted):

| truth | n | gap<2s | gap2-10 | gap10-30 | gap>30 |
|---|---|---|---|---|---|
| gap<2s | 807 | — | 0.71 | 0.19 | 0.10 |
| gap2-10 | 4,287 | — | **0.68** | 0.20 | 0.12 |
| gap10-30 | 2,974 | — | 0.49 | **0.24** | 0.27 |
| gap>30 | 2,646 | — | 0.23 | 0.17 | **0.60** |

**`gap<2s` is never predicted** — it is always absorbed into `gap2-10`. So the
four-way `ACT_NOW / WAIT_SHORT / WAIT_MEDIUM / WAIT_LONG` formulation is **not
supported by this evidence**: the model can separate "soon" from "later"
(gap>30 recall 0.60) but cannot isolate "immediately".

**Model-selection conclusion: prefer ACT-within-H at a short H (2–10s) over the
four-way timing label.** The four-way version is retained as a separately
scored output — it is informative about "later" — but it should not drive the
decision.

## 5. Is anything missing? Yes, and only for OPEN

Everything above is legitimate for EXTEND and COMPLETE, which is ~98% of V1's
decisions. What we cannot construct from existing data is market **entry**:
there are no observations of markets L5 considered and declined.

**Smallest honest collector addition** (not built, not authorized — flagged as
the one genuine gap): the recorder already serves a live sports universe to the
V1 shadow. Persisting that universe snapshot — slug, bid/ask, TTE, timestamp,
one row per market per cycle — would, combined with L5's public fills, let us
label "L5 entered / did not enter within H" over a real candidate set. It is
additive, read-only, and needs no new venue permission. Until then, `p_act` for
`last_rel=open` should be treated as **unvalidated** (PR-AUC 0.21).

## 6. V1.1 prospective-shadow design

`src/l5_v1_1_shadow.py`. Shadow by construction: no transport, no credentials,
no order verb, no `/v1/orders` string. The frozen v2 artifact is **not
modified** — Stage 1 and Stage 3 live in a separate versioned artifact,
`analysis/l5/act_wait/l5_act_wait_v1.json` (sha `c176813c250bacd0`).

```
Stage 1  ACT/WAIT      P(act within H | state, last_rel, last_size, since_last)
Stage 2  ACTION | ACT   the EXISTING v2 model, unchanged, now WITH context
Stage 3  TIMING         P(gap bucket | same key)
```

Stage 1 gates Stage 2, never the reverse, and a risk cap vetoing an ACT stays
attributed to **risk** — the two reasons never merge. A missing Stage-1
artifact fails closed to WAIT rather than falling through to the transition
model's always-act answer.

Every evaluation emits a row, WAIT included: `p_act`, `act_threshold`,
`act_tier`, `act_decision`, `predicted_gap` + full distribution,
`timing_tier`, `conditional_action_probs`, `desired_inventory_l5`,
`scaled_desired_inventory`, `last_rel`, `last_size`, `since_last_s`,
`state_l5_units` and `state_naive_our_units` (both lookups retained),
`divergence` / `divergence_reason`, `v1_would_have`, and
`l5_acted_subsequently` for prospective comparison.

`v1_would_have` on every row is the control: V1 and V1.1 scored on the same
observations, so the comparison needs no clock alignment. It is a **real V1
call at the frozen `(open, q<5)` defaults**, not a re-read of the Stage-2 row
computed with the corrected context — that shortcut would have folded the
context fix into its own baseline, and the prospective coverage split would
have come out identically zero by construction. The V1 service also keeps
running, which costs nothing and is the cleaner per-service control.

`vs_v1` attributes each disagreement to the change responsible, because two
independent changes can move the decision and a merged count could not say
which one did: `STAGE1_SUPPRESSED` (Stage 1 vetoed an act V1 took),
`CONTEXT_ENABLED` / `CONTEXT_DECLINED` (the corrected lookup reached different
evidence), `SAME`. It is kept apart from V1's own `divergence`, which means
something else entirely — desired vs executable (model / risk cap / venue).

### What this tape cannot answer, said before the numbers arrive

**L5 does not trade these markets.** L5 is on Polymarket.com; this shadow runs
on Polymarket US sports. So `l5_acted_subsequently` is emitted as **null with a
reason**, never guessed, and the prospective run splits the seven questions in
two:

| | |
|---|---|
| **Answerable here** | proposal-rate reduction; lookup-tier fractions; desired inventory trajectories V1 vs V1.1; MODEL / RISK_CAP / VENUE divergence attribution |
| **Not answerable here** | whether L5 acted after an ACT; whether a WAIT was right; Stage-2 accuracy against L5. These need L5 in the same markets, and are answered by the chronological OOS in §3 |

A second limit, found while wiring the runner: the recorder's `/status` handoff
carries **no settlement time**, so TTE is not observable either. V1 has always
run at the `1e9` sentinel and V1.1 uses the same one, so the control differs
only where it is meant to — but the TTE dimension of the state key is
**constant in shadow**, and the §3 TTE slices are not exercised prospectively.

**DEPLOYED 2026-09-16** as `l5-v1-1-shadow`, beside the unchanged V1 control.
No credentials, no transport, no mandate, no money.

## 7. Envelopes stay unarmed

Candidates A / B / C in `analysis/l5/v1_shadow/ACT_VS_WAIT.md` are preserved
**UNARMED**. Choosing between them comes after the controller can decide when
to participate, which is what this work is for.

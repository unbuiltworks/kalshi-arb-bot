# ACTIVATION — stochastic action selection + 10-market cohort

OWNER AUTHORIZATION 2026-09-17 "RESTORE FIDELITY ACTION SELECTION", reviewed
through test commit `0b6705ba`.

**This is a FIDELITY implementation correction, not a P&L optimization.**

## What changed — one environment value, pinned in the image

```
PMUS_SPRING_ACTION_SELECTION=stochastic
```

No new sampler. No change to model probabilities. The executed relation is now
the **existing** deterministic seeded inverse-CDF draw
(`pmus_l5_fidelity_live.sample_relation`), seed
`sha256(slug|decision_idx|model_sha)`, which already persisted
`seed / u / p / rank / sampled` on every decision and was already wired behind
`ACTION_SELECTION_ENV` by OWNER 5556232035 Phase 1.

**Unset is the defect, not a neutral state.** The reader is
`os.getenv(ACTION_SELECTION_ENV, "modal")`, so an image that omits the variable
silently executes the argmax. It is therefore pinned in the IMAGE, like the rung
and the market allowance, and no service variable of that name exists — verified
against the live variable list, so the image value is authoritative.

## Why

Argmax over a stochastic policy cannot select a minority relation, at any price,
in any state. Live: COMPLETE carried **p = 0.2563–0.3682 on 76 of 77 decisions**
and modal chose it **0 times**, producing one unbroken **EXTEND run of 76**,
while L5 completes **46–54%** from the same `imb1-10` states. COMPLETE is the
argmax in exactly **one** state in the whole space.

The frozen gate scores behavioral fidelity on `l1_sampled_vs_l5` — the draw —
and executable on `l1_acted_vs_l5`. Live confirmed the split: behavioral
**passed at 0.0104**, executable **failed at 0.5572**.

## Preserved independently and unchanged

target $0.10 · max leg $0.125 · k 0.00619 · maker/post-only ·
authenticated-book freshness · rest/requote · pair ceiling 0.98 · residual cap ·
monetary mandate · market-selection cohort `*` · ACT/WAIT model ·
transition probabilities.

The change alters **which relation is chosen** and nothing else. Every safety
gate applies to the drawn relation unchanged.

## FIDELITY_COMPLETE → EXECUTION_REFUSED:&lt;reason&gt;

A sampled COMPLETE the shell refuses is recorded as `hold` plus its reason and a
named divergence class. It is **never** substituted with EXTEND — verified
structurally: every `relation, refusal =` assignment in the decision path
assigns `hold`, and the classes cover `complement_not_priceable`,
`complement_preview_rejected`, `matched_pair_ceiling`,
`complement_acceptance_unproven`, `authenticated_book_unusable` and
`residual_cap`.

## Prior lifecycles — preserved, not rewritten

| | |
|---|---|
| lifecycle 1 | **−$0.1836** REALIZED |
| lifecycle 2 | **−$0.3783** REALIZED |
| cumulative confirmed realized | **−$0.5619** |
| matched-pair realized | **$0** |
| directional-residual realized | **−$0.5619** |

Both are venue-confirmed and left exactly as they settled.

## Separately recorded, deliberately NOT repaired here

`SAFETY_RESIDUAL_CAP_OVERSHOOT_2026-09-17.md` — the residual cap admitted a leg
that carried the position to **3.8636 units against a cap of 3** (~29% over).
It is a pre-trade admission test on the next action, not a bound on the
resulting position. The selector change removes the resulting *deadlock* but
does **not** repair the overshoot. Its repair alters sizing or admission and
needs its own review.

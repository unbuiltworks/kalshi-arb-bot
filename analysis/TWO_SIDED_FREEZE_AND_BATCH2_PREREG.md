# Repaired controller: FROZEN SHA + Batch-2 preregistration

## Frozen implementation

    49799e99   src/l5_two_sided.py, scripts/l5_run_two_sided_comparison.py,
               tests/test_two_sided_arm_contrast.py

Frozen BEFORE Batch 2 was selected or exported. No arm definition, threshold,
rule or metric may change before the Batch-2 run; if one must, Batch 2 is
burned and becomes development data exactly as Batch 1 did.

## Why Batch 1 is development data, not OOS

Batch 1's outcomes and economics were observed BEFORE the repaired arm contrast
was defined. Replaying repaired arms over it is regression evidence only.

## Freeze criteria, as met

| criterion | evidence |
|---|---|
| arms are provably different functions on reachable states | 22 tests in `tests/test_two_sided_arm_contrast.py`, incl. explicit divergent states |
| CONTROL differs behaviourally from REPLICATION | all 23 metrics differ on Batch-1 tape |
| `max_cycles` binds | CONTROL banks ~1 pair/window; `blocked["max_cycles"]` asserted |
| accounting identities hold | identity tests + 15/15 windows |
| no lifecycle/suite regression | identical 22-failure set at `86d55a66` and HEAD; +19 passed |
| observability is decision-neutral | Batch-1 re-run: 0 of 990 figures moved |

NOTE: observed divergence on Batch 1 is deliberately NOT a freeze criterion.
Whether the differentiating states occur in a sample is an empirical result.

## Batch-1 regression results (development evidence)

| arm | source_settled_total | shadow_total_with_flatten | matched | maker fills | takes | cancels |
|---|---|---|---|---|---|---|
| CONTROL_ONE_LEG | -0.3778 | -1.2492 | 11.11 | 19 | 8 | 27 |
| L5_REPLICATION | +2.9501 | -12.6734 | 143.52 | 240 | 127 | 322 |
| L5_ADAPTED_ECONOMIC | +2.9501 | -12.6734 | 143.52 | 240 | 127 | 322 |

Decision opportunity, Batch 1:

| arm | decisions | completion>0 | exit>0 | exit>completion | forced flatten | divergence |
|---|---|---|---|---|---|---|
| CONTROL | 22,974 | 8 | 8 | 0 | 2 | 0 |
| REPLICATION | 96,120 | 127 | 127 | 0 | 66 | 0 |
| ADAPTED | 96,120 | 127 | 127 | 0 | 66 | 0 |

REPLICATION and ADAPTED remained identical because exit value NEVER exceeded
completion value in 96,120 decisions. ADAPTED had no economically superior
action available. This was NOT fixed by adding a rule.

## Batch 2 selection rule, declared BEFORE export

Mechanical and stated in advance: the contiguous hourly block IMMEDIATELY
PRECEDING Batch 1 in the archive -- `pmus_ws_20260930_10.jsonl` through
`..._14.jsonl`, five hours. No 15Z file is passed, so the manifest excludes the
`1445z` window automatically as truncated, keeping Batch 2 strictly disjoint
from every window already manifested or replayed. Windows were NOT inspected
before selection, and none will be inspected before the single frozen run.
Requires >= 10 complete settled windows or the block is extended BACKWARDS
chronologically -- never chosen for attractiveness.

## Batch-2 questions (preregistered)

1. Does two-sided L5-like inventory management improve anything over the
   one-leg q1 structure (CONTROL)?
2. Do REPLICATION and ADAPTED differ, and if not, is it again because no
   economically superior exit exists? Zero divergence is a VALID result.
3. **Does Batch 1's adverse-selection signature repeat** -- positive matched
   economics alongside a materially negative executable unwind of the inventory
   acquired on the way there?

Reported per window, worst ten, with matched-pair economics, directional
residual settlement, fees, rebates, flatten shadow and both totals kept
separate, and residual tails and throughput reported independently.

## Explicitly out of scope until after the Batch-2 run

- Any threshold tuned from Batch 1's -15.6235 flatten result.
- Action-by-action forensics of the 1715z window.
- Any live probe, size increase, or S3 deployment.

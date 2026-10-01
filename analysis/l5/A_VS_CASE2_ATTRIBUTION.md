# A-refinement vs Case-2: clean attribution on the 18 spent 05Z-09Z windows

**SPENT-WINDOW FORENSIC ANALYSIS. Not OOS. Not validation. Not realized profit.**
No strategy parameter, arm policy, residual cap or threshold was changed. The
only differences between the states below are execution/accounting semantics.

Parity gate, run first on `cdac9807` before any state was executed: the
identical established baseline, **22 failed, 10570 passed, 28 skipped,
1 xfailed, 596.55s** -- `test_spread_scanner` x9, `test_orderbook` x5,
`test_l5_live_probe_image_closure` x2, and the six singletons in
`test_chain_ca_fallback`, `test_deploy_fallthrough_guard`,
`test_gemini_write_path_isolation`, `test_l5_live_oneshot`,
`test_preflight_gate_isolation`, `test_window_forensics_stream`.

Windows: 18 complete BTC 15m windows, 2026-09-30 0500z..0915z, 21 seen.
Tape: `pmus_ws_20260930_05..09.jsonl`. Clip 1. Arms built by the unmodified
`scripts/l5_run_variant_comparison.py`.

## The three states, and how they were isolated

Each state is a source overlay containing ONLY a different `src/`; every other
path is symlinked to the working tree, so the runner, manifest and tape are
byte-identical across all three.

| state | `_dispose` | Defect B Case 2 | Defect C | provenance |
|---|---|---|---|---|
| S0 | subtractive, exact-equality invariant | absent | repaired | `src` @ `04056a8e` |
| S1 | restated basis, `BASIS_DRIFT_TOL=1e-20` | absent | repaired | S0 + only the `a98c75ad` hunk |
| S2 | restated basis | **present** | repaired | `src` @ `cdac9807` |

`diff S0/src S1/src` is exactly five lines, all inside `_dispose`, plus the
constant. `diff S1/src S2/src` is exactly the `64682d60` hunk. So
**S0 -> S1 is the accounting effect alone and S1 -> S2 is the Case-2 effect
alone**, by construction rather than by assertion.

## S0 did not run, and was not weakened

S0 raised on the first window and produced **zero** windows:

```
AssertionError: disposal changed the basis of what remains on down:
0.1154453627180899908172635445 -> 0.1154453627180899908172635446
```

The same repeating-decimal basis that killed the earlier Case-2 rerun. It is
preserved in `attribution/S0_CRASH.txt`. **delta S0 -> S1 is therefore NOT
MEASURABLE on this window set**, and the exact-equality invariant is not a
usable baseline here at all. The invariant was not relaxed to manufacture a
full table.

## Correction to the provenance of the earlier BEFORE column

The previous report's BEFORE column came from `48ec7ba8`, and I described its
confound as "Case-2 with the A-refinement". That understated it. `src/l5_two_sided.py`
at `48ec7ba8` is the file as of `49799e99`, which contains **neither** the
Defect-A repair (`8784bfdc`) **nor** the Defect-C repair (`39d78f22`):

```
8784bfdc (Defect A) is an ancestor of 48ec7ba8: NO
39d78f22 (Defect C) is an ancestor of 48ec7ba8: NO
```

So BEFORE -> AFTER confounded **four** changes, not two: Defect A, Defect C,
the A-refinement, and Case 2. That is why this decomposition was needed.

## Result: the pure Case-2 effect

`source_settled_total`, $/window, modelled maker rebates excluded.

| arm | S1 | S2 | delta S1->S2 (pure Case-2) | t | 95% CI |
|---|---|---|---|---|---|
| CONTROL_ONE_LEG | -0.0076 | -0.0076 | +0.0000 | exact 0 | identical every window |
| L5_REPLICATION | +0.0167 | +0.0167 | +0.0000 | exact 0 | identical every window |
| **V1_EXTEND_ONLY** | **+0.3146** | **+0.3146** | **+0.0000** | exact 0 | identical every window |
| V2_EXTEND_NO_BASIS_GATE | +0.2167 | +0.2167 | +0.0000 | exact 0 | identical every window |
| **V3_CONTINUOUS_TWO_SIDED** | **+0.6626** | **+0.2147** | **-0.4479** | **-3.45** | [-0.7222, -0.1736] |
| V4_CONTINUOUS_PLUS_READJUST | +0.2167 | +0.2167 | +0.0000 | exact 0 | identical every window |

The isolation holds with no slack. Five arms report **ZERO** Case-2 refusals and
are **IDENTICAL on all 29 metrics across all 18 windows** -- not approximately
equal, bit-identical. V3 is the only arm Case 2 touches, with 272
`replenish_marketable_refused_not_resting` and 6
`replenish_queue_position_unknowable`.

| arm | maker S1 | maker S2 | matched S1 | matched S2 |
|---|---|---|---|---|
| V1_EXTEND_ONLY | 449 | 449 | 255.3 | 255.3 |
| V2 / V4 | 389 | 389 | 223.2 | 223.2 |
| V3_CONTINUOUS_TWO_SIDED | 861 | **387** | 404.2 | **219.2** |

V3 per-window: worse in **15/18**, better in 3/18, range -1.9944 to +0.4772.

## The book on V3 is closed

Under old-B semantics V3 beat V2 by +0.4459/window (0.6626 vs 0.2167). Case 2
removes **-0.4479**. Within rounding, **V3's entire advantage over V2 was the
impossible marketable-replenishment plus absent-depth queue grant**, and nothing
else. Post-repair V3 (+0.2147) sits marginally below V2/V4 (+0.2167). Level
persistence contributes nothing once executable semantics are enforced.

Restating the earlier claim correctly: of V3's total 1.5281 -> 0.2147 fall
(-1.3133), the pure Case-2 share is -0.4479, i.e. **29.3% of the original
headline and 34.1% of the fall**. The other 65.9% of the fall is the Defect-A +
Defect-C + A-refinement stack. My earlier sentence "85.9% of V3's edge was the
artifact" is right about the total but wrong to attribute that total to Case 2.

## V1 is untouched by Case 2

V1 is bit-identical in S1 and S2. Its **+0.3146/window is measured after the
accounting correction, not before it**, so the surviving figure is not an
accounting artifact -- which was the open question. What remains unmeasurable is
the split of V1's 0.5990 -> 0.3146 fall between Defect A, Defect C and the
A-refinement, because S0 will not run.

V1 is still **not validation**: 18 spent windows, replayed, with an unresolved
Case-1 fill question and no live fill evidence.

## What this does and does not license

- V3 as a mechanism is closed on the evidence, not merely doubted.
- Same-side acquisition (V1) is the only surviving candidate, and it survives
  the correction that destroyed V3.
- Case 1 -- what the venue does to an already-resting maker the market later
  crosses -- remains unresolved and unassumed. V1 rests at the touch, so Case 1
  is the one fill-semantic question that still bears on it.

# L5_CONTINUOUS_FAITHFUL — setup, provenance and assumptions

**The reviewer is right, and the repository localises the cause to one
argument.** No new strategy framework was written. Nothing was optimised.
Results are not in this document; this is what is being run and why.

---

## IMPLEMENTATION REUSED

| layer | file | label |
|---|---|---|
| behavioural surface | `src/l5_surface/engine.py` → `desired_surface` | `L5_PASSIVE_SURFACE_ENGINE_V1` |
| action classification + hard bounds | `src/gemini_execution/continuous_probe.py` → `plan`, `risk_gates`, `would_breach`, `Inventory` | `GEMINI_L5_CONTINUOUS_PROBE_V1` |
| policy selector | `src/gemini_execution/tape_replay.py` → `desired` | `POLICY_A = FROZEN_L5_NATIVE` |
| fill model | `src/gemini_execution/hypothetical.py` → `HypotheticalBook` | `QUEUE_SYMMETRIC`, rows stamped `CORRECTED_QUEUE_V1` |
| per-contract replayer | `src/gemini_execution/tape_replay.py` → `replay_window` | walks one window print-by-print to settlement |
| collector lifecycle | `src/gemini_execution/shadow_soak.py` | unchanged |

Branch `claude/gemini-btc-economic-viability-h3u7zl`. **Not one strategy line
is added, removed or tuned.** The only new files are a Dockerfile and a
railway.json.

## THE CAUSE, in one argument

```python
POLICY_A = "FROZEN_L5_NATIVE"   # the frozen policy, unchanged
POLICY_B = "GEMINI_RECYCLE"     # once residual exists, quote the COMPLETE leg
                                # and VETO the EXTEND the frozen policy asks for
```
```python
if policy == POLICY_A:
    return out
# ---- POLICY B: once residual exists, work the COMPLETE and nothing else
if inv.residual() != 0:
    for a in acts:
        (keep if a.get("action") == CP.COMPLETE else veto).append(a)
```

`shadow_soak --policy` **defaults to `FROZEN_L5_NATIVE`**. Every Gemini
service we run overrides it to `GEMINI_RECYCLE`:

```
startCommand: ... --policy GEMINI_RECYCLE ...
```

Since L5's own evidence puts `P(next action = EXTEND) = 0.66` and has **84.3%
of imbalance episodes overshooting THROUGH zero**, that override suppresses
the dominant action of the process being replicated. Every Gemini
"continuous" result in this programme was produced under it.

`replay_window` already counts `extends_vetoed` — the A/B is built in and was
always visible.

## L5 RULE PROVENANCE

Each action's controlling evidence, from `L5_OPERATING_MODEL.md` §2a
(5,546 windows, 693,916 fills, chronological TRAIN/OOS). Lines cited.

| decision | what determines it | evidence | status |
|---|---|---|---|
| **OPEN vs EXTEND vs COMPLETE** | same-side run length | `P(EXTEND) = 0.66` unconditional; run length is the ONLY feature that separates the next action and keeps its bin ordering out of sample (Spearman 1.00; run 4+ → 0.733 OOS) | **DETERMINED** |
| **same-side run behaviour** | expected run ≈ `1/(1−0.66)` = 2.9 actions | as above | **DETERMINED** |
| **stop condition** | there is none — inventory oscillates | 84.3% of episodes overshoot through zero, 1.2% land on it, **1 window of 5,546 ended flat**; no completion controller exists | **DETERMINED** |
| **side** | NOT direction, and NOT the favored side | directional momentum FALSIFIED (ρ=1.00 but symmetric: 0.672 for Δp>+0.05 vs 0.663 for Δp<−0.05); favored-side accumulation FALSIFIED (0.4919 TRAIN / 0.4847 OOS, falls to 0.4707 when pair cost > 1.02) | **DETERMINED AS A PROHIBITION** — evidence says which rules are forbidden, not which side to pick |
| **quantity** | venue `quantityMinimum` | not an L5 finding | **ASSUMPTION** (A1) |
| **price / aggressiveness** | the lane set's rung geometry | `artifacts/l5_surface/` frozen lane set | **DETERMINED by the frozen artifact**, not re-derived here |
| **TTE behaviour** | lane `TTE_policy [0, 300]`, `withdrawal_policy WITHDRAW_ON_TTE_FLOOR`, `tte_floor_s = 0` | the lane set itself | **DETERMINED — and we deviate: see A2** |
| **residual carried to settlement** | carried, never crossed out | measured as a LOSS: +$1,631 TRAIN but **−$12,971 OOS**; median residual carried into settlement worth −$144,065 across the sample | **DETERMINED — and it is a known-negative leg** |

**UNRESOLVED by L5 evidence:** which side to pick when flat. Both candidate
rules are falsified, and nothing in the source determines a replacement. The
surface quotes both outcomes and lets the market choose, which is the neutral
implementation, not a finding.

## NON-L5 ASSUMPTIONS

Declared, not buried. Each is required to make the experiment executable.

- **A1 — action size 0.01.** The venue's own `quantityMinimum` and
  `quantityIncrement`. Not an L5 quantity.
- **A2 — end-of-window floors kept.** `NO_NEW_OPEN_EXTEND_TTE_S = 60`,
  `WITHDRAW_ALL_TTE_S = 30`, both `= k × minimum_rest_time`.
  `continuous_probe.py` states plainly that the faithful lane policy is
  `[0, 300]` with `tte_floor_s = 0` and that these floors were added **for a
  probe holding real inventory**. Keeping them in a shadow is therefore a
  deviation from fidelity. Kept because they are the smaller change and are
  already in the frozen envelope; **flagged as the first thing to vary if the
  faithful run under-trades late in the window.** A documentation
  discrepancy is recorded with it: `tape_replay.py` calls these "the FROZEN
  policy's own bounds", which `continuous_probe.py` contradicts.
- **A3 — `MAX_ACTIONS = 12` does not bind in shadow.** `tape_replay.desired`
  calls `CP.plan(..., actions_taken=0)` unconditionally, so the 12-action
  live-probe cap never fires on this path. Stated because it would otherwise
  look like a silent behavioural cap; it is inert here, and no change was
  made to it.
- **A4 — risk bounds retained.** `risk_gates` untouched: imbalance ≤ 0.04,
  YES ≤ 0.10, NO ≤ 0.10, total spend ≤ 0.15; `would_breach` still refuses to
  rest an order that could not be afforded if filled. **MAX_IMBALANCE = 0.04
  is 4 × action size and is sized from L5's own run-length evidence** (modal
  run 2.9, validated regime run 4+), so it admits the oscillator rather than
  capping it at one. It is nonetheless a RISK bound, and if runs are observed
  clipping at 4 that is the bound binding, not L5 stopping.

## WHAT IS SWITCHED OFF, AND NOT DELETED

`GEMINI_ECON_ARM=0` disables `GEMINI_ECON_V1`, `PASSIVE_PAIR_V1` and the
`PP_F0/F1/F2` ladder — the spread gate, the pair-economic floor and the flow
gates. All adaptations, none of them L5. They keep running unchanged in
`gemini-econ-shadow`. The control `gemini-maker-shadow` (a44de245) is
untouched.

## EXECUTION MODEL — what is already proven

| requirement | status |
|---|---|
| YES/NO symmetry | **PROVEN as a property** — 83 inversion assertions over a self-mirrored book, including lopsided books, plus a test reconstructing the pre-`1e3159e` defect to show it would be caught (`tests/test_gemini_fill_model_side_swap.py`) |
| queue-ahead symmetry | **PROVEN** — same suite; both legs queue against the ladder an aggressor actually consumes |
| complement identity | **PROVEN** — `metadata.outcome_book` READS both outcomes' prices from the contract and checks `no_bid == 1 − yes_ask` rather than deriving it |
| quantity normalization | **ENFORCED** — every economic figure per ONE contract; `terminal.py` skips a pair whose economics carry no quantity rather than assuming |
| no future leakage | **ENFORCED** — settlement attached only as a label; a test asserts the outcome never enters a feature row |
| no rollover contamination | **ENFORCED** — `close_symbol` and `observe` filter by symbol |
| persistent inventory across the contract | **YES** — `Inventory` persists for the window; `replay_window` walks one window start to settlement |
| settlement attribution | **WIRED** — venue-published `resolutionSide`, N-read against a flapping endpoint, strike-checked, refuses to label on disagreement |

**A stale docstring corrected by the code, recorded so it is not read as
current:** `tape_replay.py`'s header still says "Gemini publishes a ladder for
the YES side and none for NO, so a NO rung's queue is UNKNOWN — treated as
zero, which is OPTIMISTIC". That describes the pre-`1e3159e` behaviour.
`size_ahead` now computes the NO queue from the ask ladder symmetrically.

## HOW IT RUNS

New service `gemini-l5-faithful`, same image, **two settings different**:
`--policy FROZEN_L5_NATIVE` and `GEMINI_ECON_ARM=0`. Own state root. BTC5m and
BTC15m reported separately and never pooled.

The policy is frozen in the tree and is not tuned. Windows from this
service's first deploy onward are the **PROSPECTIVE** set; any replay over
earlier tape is retrospective and reported separately.

## MEASUREMENT LIMITATION, stated before results

`HypotheticalBook` is an explicit **UPPER BOUND** — queue position is
displayed size at placement, and our own presence would have changed the tape
being replayed. Everything produced is **SHADOW**. No figure is realized.
Rebates are excluded from the primary outcome and reported separately only
where actually credited.

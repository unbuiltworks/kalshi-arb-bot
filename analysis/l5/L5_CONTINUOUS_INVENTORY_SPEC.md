# Blocker census, cross-venue comparison, and the shadow-arm spec

Frozen benchmark `49799e99` unchanged. Harvest checkpoint `5b1e5a51`.
Raw: `L5_BLOCKER_CENSUS.txt`, `L5_CROSS_VENUE_COMPARE.txt`.
Period: 2026-09-27..09-30, 384 L5 BTC15m markets, 33,395 grouped actions.

**Evidence status.** Simultaneous two-sided resting quoting is the leading
INFERENCE from the fill sequence (8.86% of active seconds show both outcomes
filling within one second). Orders, cancels, requotes and resting quotes are NOT
observed and are never asserted. No PMUS price is used as an L5 executable price
anywhere in this analysis.

## 1. Counterfactual blocker census (L5's OWN state only)

The frozen clone would prohibit **16,885 of 33,395 = 50.56%** of L5's actions.

| first binding gate | actions | share of all |
|---|---|---|
| `heavy_side_extend_disabled` | 16,885 | **0.5056** |

Counting gates independently (they overlap):

| gate | actions | share |
|---|---|---|
| `heavy_side_extend_disabled` (`allow_extend=False`) | 16,885 | 0.5056 |
| `extend_breaches_residual_cap` | 13,913 | 0.4166 |
| `extend_would_not_improve_basis` | 6,321 | 0.1893 |

By class: **EXTEND 16,885/16,885 blocked (100%)**; COMPLETE 0/16,066 blocked;
OPEN/REOPEN 0/444 blocked.

**Only the extend path blocks anything.** Completion and opening are never
prohibited. So the structural mismatch is exactly one thing: the clone cannot
acquire on the side it already holds.

Within EXTENDs: **37.44%** fail the basis-improvement gate, **82.40%** breach
`residual_cap=2`.

## 2. Units matter, and I previously overstated this

| L5 |residual| | median | p90 | max |
|---|---|---|---|---|
| in CONTRACTS | 63.7 | 181.0 | 592.0 |
| **in L5's own CLIPS** (median size 20) | **3.19** | **9.05** | **29.60** |

I earlier described the residual gap as "~64x" the clone's cap. In clip units it
is **~1.6x the median and ~4.5x at p90** -- a size artifact inflated that figure,
and the structural gap is far smaller than I implied. Duration-weighted, in clips:

| threshold (clips) | share of time above |
|---|---|
| 1 | 0.771 |
| 2 (current cap) | **0.577** |
| 5 | 0.217 |
| 10 | 0.045 |
| 25 | 0.0003 |

A cap of **10 clips** covers 95.5% of L5's time-weighted exposure. Nothing in the
evidence requires hundreds of contracts.

## 3. Cross-venue window-level comparison (35 overlapping epochs)

Same 15m epoch and underlying, **different books** -- shape only.

| metric (median/window) | L5 | clone | ratio |
|---|---|---|---|
| actions (L5) / fills (clone) | 72.0 | 13.0 | 5.5x |
| matched contracts | 507.2 | 5.0 | 101.4x |
| peak directional residual (contracts) | 173.4 | 1.0 | 173.4x |
| **seconds imbalanced / one-sided** | **797.0** | **690.6** | **1.2x** |
| peak residual in L5's own clips | 9.55 | 2 (cap) | - |
| same-side share of follow-ups | 0.667 | **0.000** | clone does zero extends |
| actions in final 120s | 4 (max 92) | **0** | `no_new_cycle` |
| first / last action, seconds into window | 8 / 809 | - | - |

Totals: 2,622 L5 actions vs 620 clone fills over the same 35 windows.

### The most informative row is the one with the smallest ratio

Time spent imbalanced differs by only **1.2x** (797s vs 691s). The clone is
ALREADY one-sided for ~77% of the window. It does not differ from L5 in
*tolerating* imbalance -- it differs in that **it does nothing while imbalanced**.

So the missing behaviour is precisely **acquisition during imbalance**, not
imbalance tolerance. That is a sharper and cheaper hypothesis than "carry huge
residual", and it is testable at q1-equivalent exposure.

## 4. Proposed shadow-only arm -- SPECIFIED, NOT IMPLEMENTED

Size is deliberately unchanged (`clip=1`), per PATIENCE -> FREQUENCY -> SIZE.

### Change 1: permit same-side EXTEND
- **L5 evidence**: 16,885 extends, 50.56% of all actions; 100% blocked today.
- **Current restriction**: `allow_extend=False` -> `heavy_side_extend_disabled`.
- **Proposed shadow behaviour**: `allow_extend=True` in the new arm only.
- **Falsification**: if the arm's matched throughput per window does not rise
  materially above REPLICATION's 5.0 median on a fresh OOS batch, the extend
  path is not the binding constraint and this is rejected.
- **Risk**: more one-sided acquisition, so residual and peak capital rise. Both
  are already reported per window and bounded by changes 2-3.

### Change 2: drop the basis-improvement gate on EXTEND
- **L5 evidence**: 37.44% of his extends worsen his own same-side basis; the
  gate cannot describe him. Basis-worsening extends are the high-priced ones
  (median price 0.72 vs 0.22) -- i.e. the side that became favourite.
- **Current restriction**: `extend_would_not_improve_basis`.
- **Proposed**: no basis condition on extension.
- **Falsification**: if removing it degrades `source_settled_total` relative to
  keeping it, with extension otherwise enabled, the gate was load-bearing and
  must stay. Run BOTH variants so this is separable from change 1.
- **Risk**: worse average basis, hence worse pair economics per contract; the
  pair-cost floor still applies, so a pair cannot be completed above it.

### Change 3: raise `residual_cap` from 2 to 10 CLIPS
- **L5 evidence**: median peak residual 9.55 clips; time above 10 clips is 4.5%.
- **Current restriction**: `residual_cap=2` blocks 82.40% of his extends.
- **Proposed**: 10 clips, in clip units, with clip unchanged at 1.
- **Falsification**: if realised residual settlement P&L degrades by more than
  the matched-economics gain, the cap was correct and must come back down. The
  decomposition already separates these two lines.
- **Risk**: the real one. Directional residual settlement cost scales with the
  cap; Batch 2 already showed -1.0742 of residual settlement at a cap of 2.
  This must be reported as its own line, never netted into matched economics.

### Change 4: allow ACQUISITION, not new cycles, in the final 120s
- **L5 evidence**: median 4 actions per window in the last 120s (max 92); he is
  active to a median 809s of 900.
- **Current restriction**: `no_new_cycle_near_settlement` -> clone does zero.
- **Proposed**: continue working an EXISTING position late; still no brand-new
  cycle from flat.
- **Falsification**: if late acquisitions settle worse than earlier ones in the
  same arm, the gate was protective and should be restored.
- **Risk**: least time to find a complement, so the highest chance a late leg
  settles as naked residual.

### Explicitly NOT proposed
- No size increase. No concurrency increase. No residual in the hundreds.
- No EXTEND trigger rule -- the evidence says the choice is not made (P(EXTEND)
  = 0.5124, independent of residual), so a trigger would be invention.

## 5. Why changes must be separable

Changes 1-4 must be run as separable variants against the SAME frozen
CONTROL/REPLICATION benchmark, because the census shows change 1 is categorical
(it alone unblocks everything) while 2-4 only matter once 1 is enabled. Bundling
them would leave us unable to say which produced any effect -- the exact error
the Batch-1 null result already cost us once.

## 6. Status

Nothing implemented. `49799e99` frozen. Batch 1/2 untuned. 1715z appears as one
aggregate row in the cross-venue table and has had NO forensic drill-down.
No live trading.

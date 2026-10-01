# Case-2 repair: forensic reanalysis of the SPENT 05Z-09Z windows

**LABEL: forensic reanalysis of 18 already-seen windows. NOT new OOS, NOT a
profitability validation.** It answers one question: how much of the old edge was
an artifact of Case 2? Pre-repair output preserved as `v14_PRE_CASE2.out`;
post-repair raw as `V1_V4_POST_CASE2_FORENSIC_RAW.txt`. No strategy logic,
parameter, threshold or cap changed. No live run.

## source-accounted $/window, before -> after

| arm | BEFORE | AFTER | retained |
|---|---|---|---|
| CONTROL_ONE_LEG | -0.0217 | -0.0076 | 34.9% |
| L5_REPLICATION | +0.1255 | **+0.0167** | 13.3% |
| **V1_EXTEND_ONLY** | +0.5990 | **+0.3146** | **52.5%** |
| V2_EXTEND_NO_BASIS_GATE | +0.8822 | +0.2167 | 24.6% |
| **V3_CONTINUOUS_TWO_SIDED** | **+1.5281** | **+0.2147** | **14.1%** |
| V4_CONTINUOUS_PLUS_READJUST | +0.8822 | +0.2167 | 24.6% |

Paired per-window deltas (same windows, n=18):

| arm | mean delta | se | t | 95% CI | windows worse |
|---|---|---|---|---|---|
| CONTROL | +0.0141 | 0.0145 | +0.98 | [-0.0164, +0.0446] | 1/18 |
| REPLICATION | -0.1088 | 0.0691 | -1.58 | [-0.2546, +0.0370] | 10/18 |
| V1 | -0.2844 | 0.4601 | -0.62 | [-1.2553, +0.6865] | 14/18 |
| V2 | -0.6655 | 0.3053 | -2.18 | [-1.3097, -0.0213] | 14/18 |
| **V3** | **-1.3133** | 0.4346 | **-3.02** | **[-2.2304, -0.3963]** | **17/18** |
| V4 | -0.6655 | 0.3053 | -2.18 | [-1.3097, -0.0213] | 14/18 |

Fills and matched quantity:

| arm | maker fills | taker fills | matched contracts |
|---|---|---|---|
| CONTROL | 27 -> 26 | 4 -> 5 | 13.0 -> 12.2 |
| REPLICATION | 226 -> 212 | 122 -> 113 | 142.4 -> 135.6 |
| V1 | 555 -> 449 | 252 -> 188 | 328.9 -> 255.3 |
| V2 | 569 -> 389 | 250 -> 147 | 348.0 -> 223.2 |
| **V3** | **1300 -> 387** | 206 -> 142 | **625.9 -> 219.2** |
| V4 | 569 -> 389 | 250 -> 147 | 348.0 -> 223.2 |

## V3: 85.9% of the headline was the Case-2 artifact

V3 loses **-1.3133/window of +1.5281**, t=-3.02, CI excluding zero, worse in
**17 of 18** windows. Its maker fills fall **70%** (1300 -> 387) and matched
contracts **65%** (625.9 -> 219.2).

The mechanism is explicit in the block counters, not inferred:

| V3 refusal reason | count |
|---|---|
| `replenish_marketable_refused_not_resting` | **272** |
| `replenish_queue_position_unknowable` | 6 |

So V3's level persistence was overwhelmingly persistence at a price that had
become MARKETABLE -- which our post-only live entry would have refused outright.
**V3 is now indistinguishable from V2/V4 (+0.2147 vs +0.2167), and marginally
worse.** Level persistence contributes essentially nothing once the simulator
stops granting maker fills at crossing prices. The V2->V3 step, previously the
largest contributor at +0.6459/window, is gone.

## ATTRIBUTION CAVEAT: the other arms' declines are NOT Case-2

V1, V2, REPLICATION and CONTROL record **zero** Case-2 refusals -- no
`marketable_quote_refused_not_resting`, no new `queue_position_unknowable`. Their
`desired()` returns the executable touch, which is by definition present in the
ladder and non-marketable, so neither new rule can fire for them.

Their changes come instead from the **basis-restatement in `_dispose`**, part of
fixing the crash in my own Defect-A invariant: cost is now restated as
`basis_before x remaining_qty` rather than subtracted, which removes a rounding
drift and slightly alters the basis feeding the basis gate and the completion
budget, changing trajectories over 18 windows (V1's
`extend_breaches_residual_cap` falls 124,813 -> 113,219).

**So for V1/V2/REPLICATION the before->after delta CONFOUNDS Case-2 with the
A-refinement and must not be read as a Case-2 effect.** Only V3's decline is
cleanly attributable to Case 2, because only V3 shows the refusals.

## Post-repair ordering

**V1_EXTEND_ONLY is now the best arm** at +0.3146/window, and it is the one whose
paired delta is NOT significant (t=-0.62). The ranking is
V1 > V2 ~ V3 ~ V4 > REPLICATION > CONTROL.

That is a meaningful shift: the surviving edge sits with the **narrowest
L5-derived change** -- simply permitting same-side acquisition -- rather than with
dropping basis discipline or with level persistence. Frozen REPLICATION retains
only 13.3% and is now +0.0167/window, essentially zero.

## What this does and does not establish

* **Does**: V3's attractive result was ~86% simulator gift, and the gift is gone.
  Level persistence is not an edge on this venue under conservative semantics.
  The narrow same-side-acquisition change retains the most.
* **Does NOT**: validate profitability. These windows are spent, the CIs describe
  already-seen data, and Defect-B Case 1 remains open, so even these numbers rest
  on an unresolved assumption about a resting order the market later crosses.

---

## SUPERSEDED IN PART -- see `A_VS_CASE2_ATTRIBUTION.md`

Two claims in this document are corrected there:

1. The BEFORE column came from `48ec7ba8`, whose `src/l5_two_sided.py` predates
   BOTH the Defect-A repair (`8784bfdc`) and the Defect-C repair (`39d78f22`).
   The BEFORE -> AFTER comparison therefore confounds FOUR changes, not the two
   ("Case-2 with the A-refinement") stated above.
2. The pure Case-2 effect on V3 is **-0.4479/window**, not -1.3133: 29.3% of the
   original 1.5281 headline, not 85.9%. The 85.9% figure is the whole repair
   stack. V3's advantage *over V2* was, however, entirely Case 2.

The clean S1 vs S2 decomposition also shows CONTROL, REPLICATION, V1, V2 and V4
are bit-identical across the Case-2 repair on all 29 metrics and all 18 windows,
confirming the zero-refusal reading rather than assuming it.

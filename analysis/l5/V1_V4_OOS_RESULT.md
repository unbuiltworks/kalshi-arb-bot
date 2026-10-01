# V1-V4 OOS result: 05Z-09Z, 18 untouched windows

Preregistration `0a58eb32`; implementation `17589bdc`; frozen benchmark
`49799e99` verified byte-identical at run time. Raw: `V1_V4_OOS_RAW.txt`.
Tape `pmus_ws_20260930_05..09.jsonl`, 814 MB, all 10 checksums verified.
Manifest: **18 of 21** windows complete and settled. `clip=1`,
`residual_cap=2` in every arm. One run, no tuning.

## Per-window means

| arm | matched | gross edge | fees | resid settl | flatten shadow | **source_settled** | t | shadow total | peak $ |
|---|---|---|---|---|---|---|---|---|---|
| CONTROL_ONE_LEG | 0.72 | 0.0100 | 0.0044 | -0.0272 | -0.0929 | **-0.0217** | -0.79 | -0.1146 | 0.84 |
| L5_REPLICATION | 7.91 | 0.2844 | 0.1033 | -0.0555 | -0.6882 | **+0.1255** | 1.50 | -0.5627 | 1.04 |
| V1_EXTEND_ONLY | 18.28 | 0.8873 | 0.2244 | -0.0638 | -2.4774 | **+0.5990** | 1.34 | -1.8784 | 1.99 |
| V2_EXTEND_NO_BASIS_GATE | 19.33 | 1.1854 | 0.2289 | -0.0743 | -2.5288 | **+0.8822** | **2.76** | -1.6467 | 1.93 |
| V3_CONTINUOUS_TWO_SIDED | **34.77** | 1.8099 | 0.2150 | -0.0669 | -4.2862 | **+1.5281** | **3.44** | -2.7581 | 1.99 |
| V4_CONTINUOUS_PLUS_READJUST | 19.33 | 1.1854 | 0.2289 | -0.0743 | -2.5288 | **+0.8822** | 2.76 | -1.6467 | 1.93 |

Maker fills/window: CONTROL 1.5, REPLICATION 12.6, V1 30.8, V2 31.6,
**V3 72.2** -- which is the same order as L5's own median of ~72 actions/window,
reached at clip=1.

## The falsification question, answered

> Does two-sided acquisition while imbalanced raise matched throughput and
> source-settled economics at q1 size WITHOUT residual/settlement cost growing
> faster than the added matched edge?

**On this tape: yes.** REPLICATION -> V3:

| quantity | change |
|---|---|
| matched contracts | 7.91 -> 34.77 (**4.4x**) |
| source_settled_total | +0.1255 -> +1.5281 (**12.2x**), t 1.50 -> **3.44** |
| **actual residual settlement cost** | -0.0555 -> -0.0669 (**1.2x**) |
| hypothetical flatten shadow | -0.6882 -> -4.2862 (6.2x) |
| **edge per $1 of flatten exposure** | 0.413 -> **0.422** (unchanged) |
| edge per deployed dollar | 0.274 -> **0.909** |
| peak capital | 1.04 -> 1.99 |

Source-settled economics grew 12.2x while the REAL residual settlement cost grew
1.2x, and the adverse-selection ratio that reproduced across Batches 1 and 2 is
**flat at 0.42**. The mechanism scales throughput without worsening the ratio.
V2 and V3 are the first arms in this programme whose source-settled economics are
statistically distinguishable from zero (t = 2.76 and 3.44 at n=18).

## THE RISK THAT MUST NOT BE BURIED: negative matched edge

Enabling extension lets the engine complete pairs **above $1**, producing
NEGATIVE matched gross edge in some windows:

| arm | windows with negative matched edge | worst window source_settled | worst matched edge |
|---|---|---|---|
| L5_REPLICATION | 1 / 18 | -0.5533 | -0.5000 |
| **V1_EXTEND_ONLY** | **3 / 18** | **-3.7676** | **-3.6076** |
| V2_EXTEND_NO_BASIS_GATE | 2 / 18 | -1.3843 | -1.2043 |
| V3_CONTINUOUS_TWO_SIDED | **1 / 18** | **-0.2359** | -0.1059 |

V1's worst window loses **-3.77 at clip=1**, far beyond the $0.80 bound the live
q1 probe ran under. The mean is not the risk here. Note the non-monotonicity:
V2 and V3 are *better* behaved in the tail than V1, so the basis gate was not
what protected the tail -- removing it IMPROVED the worst case. That is
counter-intuitive and is a reason for caution, not confidence.

## Residual cap: it binds everywhere, and that was predicted on the record

All **18 of 18** windows hit the cap in every variant. V3: 11,506 cap-bound
seconds (~71% of 18x900s), 286 entries and 283 exits -- so it oscillates in and
out ~16 times per window, and **283 of 283 exits coincide with the residual
falling back below the cap, i.e. an opposite-side fill reopening acquisition.**
Median residual at first bind is +-2, exactly the cap. Blocked counts split
roughly evenly by side (V3: 73,616 up / 86,717 down).

So the prediction held: the cap is the binding constraint. But the consequence I
predicted was wrong -- I expected activity "far below L5's", and V3 in fact
reaches L5's own per-window action order at clip=1. The cap throttles the
*residual*, not the *throughput*, because opposite-side fills keep reopening it.

**TWO DEFECTS IN MY OWN OBSERVER, disclosed:**
1. `acquisitions_attempted` is incremented only inside the blocked branch, so it
   equals `blocked_by_cap` by construction and `blocked_fraction` is vacuously
   1.0. The blocked COUNTS, times, oscillations and side split are sound; the
   FRACTION is not measurable from this instrumentation and is withdrawn.
2. `touch_available_while_cap_bound` (V3: 158,117) is an upper bound on missed
   chances -- a quotable touch existing while the cap forbade acquiring. It is
   not foregone economics and must not be read as such.

## V2 <-> V4 equivalence: confirmed economically

Every economic delta is exactly **0.0000**. The strict all-29-metric check
reports False, and the cause is bookkeeping only: `replenishes` (V2 has no
counter), `holds` (14,421 vs 14,466 -- V4's explicit replenish lands the leg a
tick earlier), and `blocked_reasons.extend_breaches_residual_cap` double-counted
in V4 (8,168 vs 16,336) because V4's `_replenish_price` calls `desired()`, which
increments the diagnostic, before `reconcile_quotes` calls it again. No
economic, fill, fee, settlement or residual quantity differs. The equivalence
assertion therefore PASSES on economics; my check was simply too strict, since it
included diagnostic counters.

## What V3 actually is

V3's gain over V2 (+15.44 matched, +0.6459 source-settled, +40.6 maker fills) is
LEVEL PERSISTENCE: replenishing a filled side at the price its leg held. L5's own
fill data leans against this (91% of his levels yield one fill), so this is a
**venue-specific PMUS queue effect**, not L5 replication. Holding the level keeps
queue position that re-deriving forfeits -- consistent with our own
HOLD_OPEN_EXTEND finding, and it is the single largest contributor here.

## Status

No tuning, no winner selected, no cap or size change, 1715z untouched, no live
trading. Batch 1/2 remain development data; this 05Z-09Z block was untouched
until this run and is now spent.

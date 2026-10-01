# Batch 2 — first legitimate OOS comparison of the repaired arms

Frozen implementation: **`49799e99`** (verified byte-identical at run time).
Provenance ruling: `7cbc68cd`. Raw: `analysis/BATCH2_OOS_RAW.txt`,
full tables `analysis/BATCH2_OOS_REPORT.txt`.

Tape: `pmus_ws_20260930_10..14.jsonl`, 631 MB, 5 contiguous hours immediately
preceding Batch 1, all 10 checksums verified. Manifest certified 20 of 21
windows; `1330z` and `1445z` excluded on provenance BEFORE any economics were
computed. **Primary OOS set = 18 windows.** Accounting identity: 0 violations
of 120 checks.

## Q1 — Does two-sided L5-like inventory beat the one-leg q1 structure?

Per window over 18 OOS windows:

| arm | source_settled/win | t | 95% CI | shadow_total/win | matched/win | peak capital |
|---|---|---|---|---|---|---|
| CONTROL_ONE_LEG | **+0.0067** | +4.76 | [+0.0039, +0.0094] | -0.0418 | 0.89 | 16.62 |
| L5_REPLICATION | **+0.1203** | +1.26 | [-0.0675, +0.3081] | -0.5195 | 5.00 | 17.61 |
| L5_ADAPTED_ECONOMIC | +0.1203 | +1.26 | identical | -0.5195 | 5.00 | 17.61 |

Two-sided earns ~18x more tape-backed economics and 5.6x the matched
throughput for essentially the SAME peak capital ($17.61 vs $16.62). But:

**REPLICATION's advantage is NOT statistically distinguishable from zero**
(t=+1.26, CI spans zero). CONTROL's tiny edge IS significant (t=+4.76) and
economically trivial: +$0.64/day at 96 windows/day vs REPLICATION's +$11.55/day
against a $480/day historical target.

REPLICATION pays real, tape-backed residual cost that CONTROL does not:
`residual_settlement_pnl` -1.0742 vs 0.0000. Its positive total is matched-edge
driven (+4.5496) net of fees (-1.3100) and that residual loss.

## Q2 — Do REPLICATION and ADAPTED differ? NO. Zero divergence, again.

0 of 24 metrics differ. Over **111,072 decisions**:
`completion_positive` 76, `exit_positive` 76, `exit_beats_completion` **1**,
`arm_divergence_available` **0**, `forced_flatten_available` 49.

One near-miss this time: exit value exceeded completion value ONCE, but with
non-positive value, so ADAPTED's rule (take the best strictly-positive
executable action) still had nothing to prefer. The forced-flatten path fired 49
times, so the exit machinery is live.

**This is a finding, not a defect.** The arms are provably different functions
(22 tests, explicit divergent states); those states essentially do not occur in
this market. An economically superior exit is near-nonexistent on PMUS BTC15m at
q1. No rule was added to manufacture activity.

## Q3 — Does the adverse-selection signature repeat? YES, and it is stable.

| | Batch 1 (dev, 15 win) | Batch 2 (OOS, 18 win) |
|---|---|---|
| REPLICATION matched edge / window | +0.3787 | +0.2528 |
| REPLICATION flatten shadow / window | -1.0416 | -0.6398 |
| **edge earned per $1 of flatten exposure** | **0.364** | **0.395** |
| CONTROL edge per $1 flatten | 0.244 | 0.321 |
| REPLICATION worst single-window flatten | -2.4942 | -1.2491 |

The ratio reproduces within 8% out of sample. For every dollar the inventory
would cost to unwind at executable prices, roughly **0.37-0.40 dollars** of
matched edge is banked. `shadow_total_with_flatten` is negative with
overwhelming significance (t=-5.12, CI [-0.72, -0.32]).

CONTROL shows the SAME SIGN at ~1/13 the magnitude, so this is a property of
L5-style incremental acquisition generally, amplified by cycling -- not an
artifact of two-sided quoting alone.

## Honest reading

1. Matched-pair accounting looks profitable while mid-flight inventory is
   persistently expensive to unwind. Both batches agree.
2. The flatten shadow is HYPOTHETICAL -- these windows all settled, and the
   real residual cost is the -1.0742 settlement line, not -11.5172. The flatten
   shadow measures fragility/adverse selection, not a realized loss.
3. On actually-settled economics two-sided is better, but not significantly so
   at n=18, and nowhere near the $480/day target at q1.
4. Residual tails did NOT widen to buy the improvement: max directional
   residual is 1 contract for every arm in every window (clip=1 binds).

## Not done, deliberately

No threshold tuned from either batch. No forensics of 1715z yet. No live probe,
no size increase, no concurrency change, no S3 deployment.

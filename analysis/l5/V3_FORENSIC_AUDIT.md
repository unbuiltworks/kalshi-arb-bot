# V3 forensic attribution and simulator-validity audit

Development tape (Batch 2, 10Z-14Z). The 05Z-09Z OOS block is spent and was NOT
re-run. Raw: `V3_SIMULATOR_AUDIT_RAW.txt`, `V3_FORENSICS_RAW.txt`.

## THE HEADLINE: a cost-accounting bug in the FROZEN engine

`src/l5_two_sided.py`, `decide_completion()`, EXIT_NOW branch:

    self.qty[held]  -= held_qty
    self.cost[held] -= self.basis(held) * held_qty   # basis() recomputed AFTER

`basis()` is `cost/qty`, so evaluating it after the quantity has been decremented
uses the WRONG denominator. A flatten therefore changes the average cost of what
remains, and when it closes a side entirely `basis()` returns 0 (its `q > 0`
guard) so the cost is never removed at all. Demonstrated directly:

| held qty | flattened | basis before | basis after | cost left | qty left |
|---|---|---|---|---|---|
| 2 | 1 | 0.40 | **0.00** | 0.00 | 1 |
| 2 | 1.5 | 0.40 | **-3.2** | -1.60 | 0.5 |
| 2 | 2 | 0.40 | 0 | **0.80 (should be 0)** | 0 |
| 3 | 1 | 0.40 | **0.30** | 0.60 | 2 |

Every case corrupts. After a FULL flatten the stale cost contaminates the next
acquisition: buying 1 lot at 0.50 then yields `basis = 1.30`, impossible for a
binary outcome.

### This answers Q1 exactly: why >$1 pairs are possible

`_bank_pairs` builds `Pair(new, self.basis("up"), self.basis("down"), ...)`. With
a corrupted basis the pair's recorded leg prices exceed $1 and gross edge goes
negative. The worst observed pairs are not trades -- they are accounting debris:

| arm | worst pair |
|---|---|
| V1 | `up_px=5.6500 down_px=5.14 cost=10.79 edge=-9.79` |
| V2 | `up_px=5.6500 down_px=5.15 cost=10.80 edge=-9.80` |
| V3 | `up_px=0.7600 down_px=2.5300 cost=3.29 edge=-2.29` |
| REPLICATION | `up_px=0.9400 down_px=0.5900 cost=1.53 edge=-0.53` |

A leg price of 5.65 is prima facie impossible. The frozen benchmark is affected
too (3 of 100 pairs above $1). NOT PATCHED, per instruction; pinned by a strict
`xfail` test in `tests/test_two_sided.py` so a silent future fix cannot pass
unnoticed.

### Q2: why V1's tail is worse than V2's

It is NOT the basis gate protecting anything. V1 and V2 produce the SAME corrupted
pair in the same window (1130z, `up_px=5.65`, down 5.14 vs 5.15). The tail is
driven by flatten-induced basis corruption, which is path-dependent: the arms
flatten at different moments, so different amounts of stale cost accumulate in
different windows. The V1-vs-V2 tail difference is an artifact of that path, not
an economic property of basis discipline. This also explains why removing a
nominally protective gate IMPROVED the worst case -- a result that should have
been a red flag, and was.

## Q3: why LEVEL PERSISTENCE increases throughput and apparent economics

Per-fill ledger:

| arm | fills | persisted | crossed | crossed share | **ahead0 = 0** | med leg age |
|---|---|---|---|---|---|---|
| L5_REPLICATION | 253 | 0 | 197 | 0.779 | 0.020 | 6.5s |
| V1 | 673 | 0 | 561 | 0.834 | 0.002 | 6.5s |
| V2 | 670 | 0 | 535 | 0.799 | 0.002 | 6.1s |
| **V3** | 1586 | 1201 | 1459 | **0.920** | **0.725** | 10.2s |

Within V3:

| | n | crossed | **ahead0 = 0** | maker share |
|---|---|---|---|---|
| persisted | 1201 | **0.947** | **0.953** | 0.983 |
| derived | 385 | 0.836 | **0.013** | 0.535 |

**The queue interpretation is REJECTED.** V3 does not earn queue position by
holding it. A persisted price is usually absent from the current ladder, and
`displayed_at` treats an absent level as empty -- "we would be first in queue" --
so persisted legs receive `ahead0 = 0` in 95.3% of fills versus 1.3% for
re-derived legs. The simulator GRANTS the advantage.

Compounding it, 94.7% of persisted fills were CROSSED (`leg.px >= own_ask`) yet
98.3% were booked MAKER. A real resting buy at or above the ask executes
immediately as a TAKER at the ask, paying the 0.0695 taker fee instead of
collecting the maker side. Those fills are mispriced and misclassified. Nothing
in `_still_valid` checks for crossing.

**And this is not V3-specific: frozen REPLICATION is 77.9% crossed.** With
tick-wide spreads a single adverse tick leaves any resting quote crossed, so the
crossing assumption contaminates Batch 1, Batch 2 and the 05Z-09Z block alike.

Conclusion: V3's +$1.5281/window is NOT executable evidence. Neither is V2's
+$0.8822 nor frozen REPLICATION's +$0.1255 until crossing is resolved.

## Paired statistics: no single step is significant

n = 18, historical replay, not realized profit.

| step | mean delta | se | t | 95% CI | windows improved |
|---|---|---|---|---|---|
| REPLICATION -> V1 | +0.4735 | 0.4166 | +1.14 | [-0.4055, +1.3524] | 14/18 |
| V1 -> V2 | +0.2832 | 0.3737 | +0.76 | [-0.5053, +1.0717] | **8/18** |
| V2 -> V3 | +0.6459 | 0.4791 | +1.35 | [-0.3650, +1.6567] | 12/18 |
| **REPLICATION -> V3** | +1.4025 | 0.4656 | **+3.01** | [+0.4201, +2.3850] | 16/18 |

Only the cumulative change clears zero. The per-arm t-statistics I reported
earlier (V2 2.76, V3 3.44) test each arm against ZERO, not against the arm below
it, and overstated the per-step evidence. V1 -> V2 improves only 8 of 18 windows.

## Q4: the 72.2 maker fills coincidence

L5's ~72 is grouped observed actions on another venue and account; V3's 72.2 is
simulated PMUS maker fills of which 92% were crossed and 72.5% got free queue
priority. The similarity is a scale coincidence and is NOT validation.

## Q5: cap -- no expansion

Confirmed, and now doubly so. Cap=2 binds in 18/18 windows yet V3 still reached
34.77 matched/window because 283 of 283 cap exits coincided with an opposite-side
fill reopening acquisition. Binding frequency alone does not justify expansion,
and the throughput that would have argued for it is artifact-inflated.

## Instrumentation fixes (development-only, no OOS re-run)

1. **Observer denominator**: `acquisitions_attempted` now counts EVERY heavy-side
   opportunity, blocked or not, so `blocked_fraction` is real. Previously it was
   incremented only in the blocked branch and was vacuously 1.0.
2. **V4 diagnostic double-count**: deciding whether to replenish requires asking
   what the frozen gate allows, which was inflating the block counters. The probe
   now snapshots and restores `self.blocked`, and `desired()` is called once.
   V2/V4 equivalence is asserted on decisions and economics, not on
   implementation-specific counter values.

62 tests pass, 1 strict xfail pinning the flatten bug.

## Status

Nothing patched in the frozen engine. No cap change, no size change, no tuning,
1715z untouched, no live trading.

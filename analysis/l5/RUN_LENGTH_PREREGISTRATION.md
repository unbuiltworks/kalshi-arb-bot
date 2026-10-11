# Step C pre-registration — same-side run length

**Frozen before any candidate's P&L was computed.** Everything below is read
off L5's own behaviour on the **development half only** (chronologically the
earlier 889 windows of the 1,779 settled windows in
`analysis/l5/l5_canonical_sequence.csv.gz`). The held-out half is not
consulted until the candidates are scored.

## Definitions

A **heavy-side EXTEND** is an action that adds to the side L5 is already long.
**Run length** is the number of consecutive heavy-side adds that preceded it,
so run 0 is the first add onto an existing residual.

**Edge** is `E[won] − price`. The venue price *is* the market's probability, so
a feature that only moves the price carries no decision-time information. Only
a feature that moves `E[won] − price` does.

Decisions use decision-time state only. `settlement_outcome` enters
evaluation and never a rule.

## Decision set

35,578 heavy-side EXTENDs across 1,779 settled windows (DEV 17,381 / OOS
18,197). The corpus is 965 March + 1,404 April + 33,209 August decisions, so a
50/50 split by window count puts the boundary at 2026-08-29.

## What L5 himself does as a run extends (DEV)

| run | n | clip | price | maker % | price vs own-side mean |
|---|---|---|---|---|---|
| 0 | 5,926 | 22.93 | 0.4938 | 63.6% | +0.0196 |
| 1 | 3,416 | 22.40 | 0.4754 | 69.5% | +0.0123 |
| 2 | 2,112 | 19.52 | 0.4457 | 74.7% | +0.0050 |
| 3 | 1,411 | 17.97 | 0.4196 | 78.3% | −0.0020 |
| 4 | 990 | 16.20 | 0.3863 | 84.0% | −0.0109 |
| 5 | 723 | 16.19 | 0.3543 | 89.1% | −0.0240 |
| 6 | 538 | 16.09 | 0.3329 | 92.0% | −0.0296 |
| 7 | 401 | 14.81 | 0.3111 | 93.3% | −0.0375 |
| 8+ | 1,864 | 14.31 | 0.2309 | 95.9% | −0.0574 |

He de-escalates continuously — clip −38% (22.93 → 14.31; an earlier
whole-corpus figure of 17.00 → 8.50 used a different sample and is not the
registered one), maker share +32pp — and never stops.
5.4% of DEV extends are at run ≥ 12. **There is no breakpoint in his own
behaviour**, so any threshold is ours, not his, and is registered as such.

The price column falling is not by itself evidence of deliberate skew: a run
happens *because* price moved against him, so "later in the run" and "further
into an adverse drift" are the same thing in this tape and cannot be separated.

Clip versus **residual magnitude** is flat (19.39 / 18.53 / 18.36 / 20.39 for
`r<3 / r<10 / r<30 / r>=30`), and relative price is flat until `r>=30`
(+0.0193 / +0.0290 / +0.0212 / −0.0056). **L5 does not skew by inventory
size.** Grok's asymmetric-skew premise has no behavioural support in the
dimension it names.

Clip versus **time to settlement** rises into the close (17.61 at `t>=420`,
25.20 at `t<180`, 22.51 at `t<60`) and maker share falls to 58.4% in the last
minute. Late extends are bigger and more often taker.

## Pre-registered candidates

Thresholds chosen from the table above, before seeing any P&L.

| candidate | rule | behavioural basis | DEV bite |
|---|---|---|---|
| `RUN_SUPPRESS_3` | refuse a heavy extend at run ≥ 3 | where his clip first falls below 80% of run 0 and his relative price crosses from above to below his own mean | 26.0% of extends |
| `RUN_SUPPRESS_5` | refuse at run ≥ 5 | clip at 71% of run 0, maker share past 89%; 84th percentile of the run distribution | 16.1% |
| `RUN_SUPPRESS_8` | refuse at run ≥ 8 | clip plateaus at 14.3, maker share saturates at 96% | 9.0% |
| `LATE_NO_EXTEND_60` | refuse a heavy extend with TTE < 60s | the one regime where he sizes *up* and turns taker | 7.1% |
| `COMPLEMENT_REPAIR` | no same-side add while residual ≠ 0 | existing comparator (run ≥ 0) | 100% |
| `RESIDUAL_AGE_120` | no deepening of an imbalance older than 120s | existing comparator | — |
| `RESIDUAL_CAP_k2`, `k4` | cap `\|residual\|` at k × running max clip | existing comparator | — |
| `PAIR_COST_CAP_0.96` | refuse a completing leg pricing a pair above 0.96 | the written spring rule | — |
| `RANDOM_take<r>` | state-blind suppression at each candidate's own take rate | the control that says whether a rule has content beyond trading less | matched |

Grok-style inventory-magnitude skew is **not** pre-registered as a candidate:
the behaviour it assumes is absent from the tape.

## Fill classification for counterfactuals

Window total P&L is exactly `Σ over contracts (won − price)`, which is
invariant to the pairing convention (verified: FIFO and LIFO both give
+$2,758.58 across the settled corpus). So removing an action changes total
economics by exactly `−(won − price)` on its contracts and changes no retained
contract's total economics. The uncertainty is therefore **behavioural, not
accounting**:

- **VOID** — the suppressed contracts. Their removal is exact.
- **DETERMINATE** — the first suppression in a window, and every action before
  it. Their state is the native state, so the decision to suppress is sound.
- **UNCERTAIN** — every action after a window's first suppression. We are
  assuming L5 would still have offered them from an inventory state we
  changed. The tape cannot support that.

A candidate is scored twice: `Δ_determinate` from first-suppression contracts
only, and `Δ_total`. **A candidate whose advantage lives in `Δ_uncertain`
fails.**

## Decision rule, fixed in advance

`RUN_LENGTH_SIGNAL_INCREMENTAL = YES` only if the pooled within-stratum edge
difference between run ≥ k and run < k is negative in **both** chronological
halves with the full control set and its clustered 95% interval excludes zero
in the held-out half.

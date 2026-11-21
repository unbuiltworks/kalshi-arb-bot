# Residual-repair counterfactual — PRE-REGISTRATION

Written and committed **before any candidate was scored**. Thresholds below are
fixed from DEV *behavioural* quantiles only. No settlement outcome, and no P&L,
entered the choice of any threshold or any split.

## The question

B' (RESIDUAL_CAP_k2 / PROSPECTIVE_NET) bounded inventory successfully and still
lost money, because residual losses overwhelmed positive matched economics:

| line | frozen value |
|---|---|
| matched P&L | +$1.027880 |
| residual P&L | −$2.580880 |
| realized net | −$1.553000 |

> **Can we materially reduce the −$2.580880 residual loss without destroying
> the +$1.027880 matched engine?**

Scope: PMUS adaptation overlays only. `L5_NATIVE` (frozen `030d8bf`) is
UNCHANGED. No deployment. No strategy redesign.

## Corpus and splits (chronological, fixed here)

`analysis/l5/l5_canonical_sequence.csv.gz` — 2,216 settled windows, 71,984
actions, L5's own Polymarket activity.

| split | dates | windows | actions | role |
|---|---|---|---|---|
| **AUG_DEV** | 2026-08-28 → 08-29 | 968 | 32,351 | **selection only** |
| **AUG_OOS** | 2026-08-30 → 08-31 | 1,032 | 34,364 | chronological out-of-sample |
| **SPRING** | 2026-03-19 → 04-21 | 216 | 5,269 | out-of-ERA check (pre-incentive, ~all 15m) |
| **LIVE** | 2026-09-27 PMUS BTC15m ×4 | 4 | — | **validation only, never selection** |

The August era is mature incentive-era L5, whose near-$1 pair tolerance is
locked for us. SPRING is the era our spring-discipline actually emulates, so it
is reported separately and is not pooled into the selection set.

A candidate is chosen on **AUG_DEV alone**. It must then beat B' on **AUG_OOS**
to be returned at all. SPRING and LIVE are reported, never selected on.

## Behavioural anchors measured on AUG_DEV (decision-time fields only)

| quantity | median | p75 | p90 |
|---|---|---|---|
| clip size | 13.514 | 44.000 | 50.000 |
| \|residual\| (nonzero) | 122.217 | 217.150 | 324.742 |
| residual episode age at decision | 105 s | 252 s | 443 s |
| time to settlement | 357 s | 596 s (p25 168 s, p10 78 s) |

Note for context, not for selection: mature L5 carries a median \|residual\| of
**9.04× its median clip**. Our live k=2 cap is far tighter than L5's own
behaviour, and still lost on residual.

## Treatments (the smallest set that addresses the stated failure)

Each is an overlay on the admitted-action stream. Each may only ever **refuse**
an action; none invents a trade, changes a price, or changes a size.

- **T1 `HEAVY_EXTEND_BLOCK(m)`** — refuse a heavy-side EXTEND (an action adding
  to the side already long) while `|residual| > m × running_max_clip`.
  Pre-registered grid **m ∈ {1, 2, 4}**. m=2 is B'-equivalent in spirit and is
  the reference point.
- **T2 `COMPLEMENT_ONLY`** — while residual ≠ 0, admit only actions that reduce
  `|residual|`. The strict form. No parameter.
- **T3 `RESIDUAL_AGE(T)`** — refuse to DEEPEN an imbalance older than `T`.
  Repair always allowed. Grid **T ∈ {105, 252, 443} s** = the DEV median/p75/p90
  of residual episode age.
- **T4 `TTE_COMPLEMENT(τ)`** — inside `τ` seconds of settlement, refuse any
  action that increases `|residual|`; outside `τ`, no constraint. Grid
  **τ ∈ {78, 168, 357} s** = DEV p10/p25/median of time-to-settlement.
- **Combinations** — formed ONLY between treatments that are individually
  justified on AUG_DEV (each must beat B' on DEV net on its own). No blind
  crossing of the grid.

`B'` itself (`RESIDUAL_CAP_k2` on prospective net) is the incumbent and the bar.
`NATIVE` (take everything) is the floor control.

## Counterfactual accounting — D / U / V

The existing replay assumes a suppressed action simply does not happen and
every later fill still occurs. **That assumption is rejected here**, per the
governing instruction: changing an earlier action invalidates downstream fills
that would no longer be executable.

For each window and each candidate:

- **VOID** — the contracts of every suppressed action. Never credited.
- **DETERMINATE** — every action up to and including the first suppression in
  that window. Both the candidate and the comparator are scored on this same
  prefix, so the comparison is like-for-like.
- **UNCERTAIN** — every action after the first suppression. Reported
  separately, never summed into the primary score.

**The DETERMINATE score decides.** The UNCERTAIN (full-window, fill-invariant)
score is reported for context only and cannot promote a candidate. A window
with no suppression is wholly DETERMINATE.

## Scoring

Primary, per the governing instruction:

```
net = matched P&L + residual P&L + supportable maker economics − fees
```

- **supportable maker economics = $0.** Rebates are confirmed zero three
  independent ways and nothing has ever been credited. A zero commission is not
  a rebate. No rebate is assumed anywhere.
- **fees** = PMUS schedule, `fee = theta * C * p * (1-p)`, taker theta 0.0695,
  applied to taker fills only. Taker rebate tiers begin at $250k; this account
  is below and is counted at zero.

Also reported for every candidate, as required:

matched contracts retained · maker-fill volume retained · residual dollars
eliminated · action retention · contract retention · max \|residual\| ·
$ per settled window.

## Decision rule (fixed in advance)

A candidate is **returned** only if, on **AUG_OOS**, using **DETERMINATE**
accounting, it:

1. beats B' on net `$/settled window`; **and**
2. retains **≥ 60%** of B's matched contracts (the matched engine must survive —
   this is the "without destroying the matched engine" clause, made numeric);
   **and**
3. retains **≥ 50%** of B's maker-fill volume (scalability floor); **and**
4. does not increase max \|residual\|.

Among candidates that pass, return the **smallest** — fewest treatments, then
fewest parameters, then the least restrictive parameter. Ties are broken toward
the simpler rule, never toward the better number.

If no candidate passes, the honest answer is **NO** and that is what will be
reported. Failure to find one is a result, not a reason to widen the grid.

## What is explicitly NOT permitted here

- No selection on the four live windows.
- No selection on AUG_OOS or SPRING.
- No threshold chosen by sweeping P&L.
- No change to `L5_NATIVE`, the $5 envelope, maker-only behaviour, pair-cost
  policy, loss halt, or k.
- No deployment.

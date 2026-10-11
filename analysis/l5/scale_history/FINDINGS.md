# SMALL-L5 → RAMPED-L5: L5 scaled 87×, and not by trading bigger

Evidence only. Nothing in the live R1 campaign, the V1 controller, k, the pair
target or any risk parameter was changed on the strength of this.

## 0. The gating question: no collector is needed

The existing raw public-activity dumps in `data/l5/raw/` (`activity.jsonl` +
`activity_gap.jsonl.gz`) hold **763,966 unique TRADE fills across 128 days**,
2026-03-19 → 2026-08-31. The requested 2026-03-16 → 2026-05-15 interval is
covered by **42 days and 87,993 records**. No Goldsky/Polygon backfill is
required for this question.

`l5_canonical_sequence.csv.gz` looked like a gap — 71,984 fills over only 22
days — and is not one. It is a **window-selected** corpus: the subset
reconstructed into complete up/down windows for the behavioural model. A
day-level scale question has to be asked of the raw tape, which is complete.

De-duplicated on `(transactionHash, asset, timestamp, size, price)`; every row
keeps its hash and conditionId, so any figure below walks back to a chain
record.

**Maker/taker is not reported.** The public activity feed carries no aggressor
flag. It is absent here rather than guessed.

## 1. Breakpoints, discovered rather than assumed

Binary segmentation on the mean, deterministic, no tunable that could be nudged
until a break landed on an expected date.

| series | change points |
|---|---|
| **fills/day** (activity) | **2026-04-20**, 07-15, 08-11, 08-17, 08-22 |
| **notional/day** (capital) | **2026-07-14**, 08-11, 08-17, 08-22 |
| median fill size | 03-27, 04-13, 06-24, 06-29, 07-16, 07-20, 07-24, 08-05 |
| residual/matched | 04-17, 06-15, 07-01, 07-20, 08-17 |
| switch rate | 03-27, 03-31, 04-06, **04-21**, 04-28, 07-16, 08-17 |
| run length | 04-06, 04-14, **04-21**, 04-28, 06-23, 08-10 |

**April 9 is a real step but a minor one, and it is not the break.**

| period | fills/day | notional/day | median size |
|---|---|---|---|
| Apr 5–8 | 82 | $380 | 10.0 |
| Apr 9–12 | 405 | $1,820 | 10.0 |
| Apr 13–19 | 455 | $2,975 | 20.0 |
| **Apr 20–26** | **2,457** | **$21,820** | 19.2 |

April 9 raises activity ~5×; April 20 raises it another ~5× on top and is where
the segmentation actually breaks. Activity and capital also break at **different
times** — activity in April, capital in July — which a single assumed boundary
would have hidden entirely.

## 2. Primary test: capital rose while normalized shape held

Regimes taken from the empirical breaks, not from the suggested boundaries.

| regime | days | fills/d | notional/d | med size | p90 size | markets/d | $/window |
|---|---|---|---|---|---|---|---|
| **A** pre-break (03-19→04-19) | 16 | 218 | $1,139 | 10.00 | 10 | 9 | $106 |
| **B** activity-ramp (04-20→07-13) | 69 | 3,432 | $26,510 | 19.57 | 30 | 40 | $677 |
| **C** capital-ramp (07-14→08-31) | 43 | 9,700 | $99,047 | 14.00 | 50 | 219 | $404 |

Normalized, scale-free shape over the same regimes:

| regime | residual/matched | switch rate | run length | pair cost |
|---|---|---|---|---|
| A | 0.3556 | 0.2275 | 2.45 | 0.9809 |
| B | 0.2260 | 0.2740 | 3.33 | 0.9849 |
| C | 0.3594 | 0.2500 | 2.00 | 0.9951 |

**Answer: yes.** Capital rose **87×** (notional/day $1,139 → $99,047) while
residual/matched, switching frequency, same-side persistence and pair cost stayed
in a narrow band — A and C are nearly identical on residual/matched (0.3556 vs
0.3594) despite an 87× capital difference.

## 3. The finding that matters most, and it refutes the scaling model

**`q_later(t) = k × q_early(t)` is the wrong description of L5's own ramp.**

Across an 87× capital increase, per-fill size barely moved:

| | A → B | A → C |
|---|---|---|
| notional/day | 23.3× | **87.0×** |
| fills/day | 15.7× | **44.5×** |
| markets/day | ~4.4× | **24×** |
| **median fill size** | **1.96×** | **1.40×** |
| p90 fill size | 3.0× | 5.0× |

L5 scaled by **frequency and breadth**, not by size. Per-fill size is close to
scale-invariant: a 44× increase in fills and a 24× increase in markets carried
almost all of the capital growth, while the typical fill went from 10 shares to
14.

This is L5's own history confirming the operating model's stated order —
*patience → frequency → size → concurrency*, "favor increasing frequency before
size". It was doctrine; it is now measured.

## 4. The smallest L5 regime that already has the mature shape

Regime A, 2026-03-19 → 2026-04-19, 166 windows with ≥4 fills:

| per window | p50 | p90 | max |
|---|---|---|---|
| notional | **$128.69** | $350.44 | $836.96 |
| fills | 28 | 52 | 92 |
| median fill size (shares) | 10.00 | 20.00 | 20.00 |
| peak residual (shares) | 70.00 | 160.00 | 310.20 |
| **peak residual ($)** | **$32.90** | $75.20 | $145.79 |

The mature continuous-inventory shape — residual/matched ≈ 0.36, switch rate
≈ 0.23, run length ≈ 2.5, pair cost ≈ 0.98 — is **already fully present at
~$1,139/day and ~$129/window**, with a 10-share typical fill.

## 5. What this means for V1's k — the number is not 0.006

Our shape floor k ≈ 0.006 was derived against L5's **mature worst window**
($4,848 notional, $466 peak residual). Measured against the regime where the
shape is already present, the same Envelope A is far less of a scale-down:

| Envelope A ceiling | vs L5-early p50 window | vs L5-early worst window |
|---|---|---|
| exposure $30 | k = **0.233** | k = **0.036** |
| residual $3 | k = **0.091** | k = **0.021** |

Even against L5-early's *worst* window, $30/$3 implies **k ≈ 0.021–0.036 —
3.5× to 6× above the 0.006 shape floor**, and against a typical early window it
is ~0.09–0.23, i.e. 15–39× above it.

So the constraint is much weaker than the mature-window calibration suggested.
Targeting L5's early regime rather than its mature regime, Envelope A sits
comfortably inside shape-preserving territory with room to spare.

**The honest caveat on that comparison:** k against L5-early and k against
L5-mature are different quantities and must not be merged. The deployed V1
shadow runs k = 0.00619, calibrated to the mature corpus, and this analysis does
not change it. What it establishes is that the *floor* is not the binding
consideration people feared, and that if V1 is meant to reproduce the regime
where the shape first appears, the relevant scale is an order of magnitude
larger than 0.006.

## 6. Not claimed

- **Maker/taker**: no aggressor flag on this tape. Not inferred.
- **Fees / settlement P&L**: the activity feed carries no fee field and no
  settlement cashflow. Per-window `pair_cost` here is a *cost* trajectory built
  from fills, not a realized result, and is labelled as such.
- Regime C's markets/day (219) partly reflects L5 moving into more markets, not
  only more capital; activity and capital breaking at different dates is the
  evidence for that and it is reported rather than resolved.

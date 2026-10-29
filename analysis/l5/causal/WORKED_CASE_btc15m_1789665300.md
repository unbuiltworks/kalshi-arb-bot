# Worked case — `btc-updown-15m-1789665300`, public fills → parent orders → book

A worked example inside `PARENT_ORDER_SURFACE_GEOMETRY_V1` /
`L5_PASSIVE_SURFACE_SPEC_V1`. Not a new branch.

**Identity verified independently, not accepted from the lead.** The
conditionId was read from **L5's own activity rows**:
`0xa1a869306d77fc4643ee7816eb05f97f53223c8cdd6627f850155b63fefff300` — matching
the external reconstruction exactly. So were the **58 TRADE rows, 37 Down / 21
Up, all BUY**, spanning 1789665336 → 1789666048 across **58 distinct
transactions**.

## The collapse

| unit | count | |
|---|---|---|
| public fills | **58** | what the activity feed shows |
| same-second (t, outcome, price) clusters | 48 | −17.2%, heuristic |
| **chain-proven parent orders** | **37** | **−36.2%**, evidence |
| unresolved | **0** | every receipt available |

**Two labels never merged.** `L5_OWNED` — the chain names L5 as the order's
owner. `L5_COMPATIBLE` — anonymous book liquidity at a price L5 later executed
at; it may be his order or anyone's.

Final inventory from parent executions: **Up 759.84 / Down 904.94, residual
−145.10**.

## A. Simultaneous distinct parents at multiple prices — YES, and the shape is specific

Two timestamps carry more than one distinct parent first-executing, and **both
are the same outcome one tick apart, each pairing a 100-lot with a small lot**:

| t | parents |
|---|---|
| 1789665796 | **Up @ 0.90 → 100.00** and Up @ 0.89 → 33.38 |
| 1789666000 | **Down @ 0.08 → 100.00** and Down @ 0.09 → 2.00 |

Two same-outcome parent pairs also have **overlapping execution intervals at
different prices**. A ladder is directly observed, at 1-tick spacing.

## B. UP/DOWN overlap — not at the second scale, clearly at the minute scale

16 Up parents, 21 Down. Distance to the nearest opposite-outcome parent:
**median 26 s** (p25 11 s, p75 41 s, max 91 s).

| within | share |
|---|---|
| 1 s | **0.0000** |
| 5 s | **0.0000** |
| 15 s | 0.3784 |
| 30 s | 0.5946 |
| **60 s** | **0.8919** |

Consistent with the population figure (median 18 s over 1,015 pairs) and a
little slower. **Two-sided presence is supported on a ~30-second cadence, not
as simultaneous placement.**

## C. Do the odd fragments collapse into round parents — YES, decisively

| what the feed showed | what the chain shows |
|---|---|
| **30 / 10 / 40 Down @ 7¢, t=1789665786** | **ONE parent, `0x03bb5ae0c1f1b2da`, exactly 80.00, 3 fragments** |
| two ~1-share Down @ 2¢, t=1789666048 | ONE parent, `0xf3c91cc5a3d8414e`, **1.99** (0.972 + 1.0204) |
| 100 Down @ 8¢, t=1789666000 | ONE parent, `0x4cb7e8b641c7bef6`, **exactly 100.00**, single execution |
| 92.51 Down @ 7¢, t=1789666009 | ONE parent, single execution, **not round** — consistent with a partly-filled 100-lot, and `executed_qty` is a lower bound |
| 9¢ Down at t=1789665771 and t=1789665783 | **TWO separate parents** (1.10 and 5.00), 12 s apart — *not* one order |

The headline case is unambiguous: **30 + 10 + 40 is one round order of 80.**

## D. The 100-share mode — present

**5 of 37 parents executed exactly 100.00 (13.5%)**, against 18.9% in the
1,142-parent population. Plus 99.97 and 92.51 as near-misses.

Full quantity list: 1.1, 1.99, 2.0, 3.37, 3.56, 4.23, 5.0, 5.0, 5.0, 5.07,
15.38, 17.5, 18.57, 21.17, 21.23, 23.62, 27.74, **30.0**, 33.38, 35.21,
**48.0**, 49.99, 53.91, 63.33, **68.0**, 70.52, 71.85, **80.0**, 86.59, 92.51,
99.97, **100.0 ×6**.

## E. Does parent size respond to inventory — NO

| | |
|---|---|
| corr(total inventory before, parent qty) | **−0.0029** |
| corr(\|residual\| before, parent qty) | **−0.1296** |

The 100-lots appear at inventory totals of **15.4, 346.9, 721.4, 1343.5,
1445.5** — across the entire range — while the residual swung **0 → +76.2 →
−198.9 → −145.1**.

**The lot is fixed while inventory oscillates by nearly 275 shares.** This is
the sharpest single-market version of the population result, and it is why the
spec records inventory conditioning as *weak and non-monotone* with LOW
confidence.

## F. Was the execution price present in the book beforehand — partially, and coverage-limited

**Full-ladder coverage reached only 1 of 37 parents.** The recorder's
continuous full-ladder capture began at 1789666032; this window ran
1789665300 → 1789666200. Summaries (touch only, ~5 s cadence) cover
1789665565 → 1789666320, reaching 29 of 37.

At that resolution, **20 of 29 parent executions (69%) sat AT or INSIDE the
touch**, and the modal reading is `ticks_in = 1.0` — *the execution price equals
the bid*, which is the resting-maker signature. 8 read above the ask and 1 below
the bid; at a 5-second summary cadence **this data cannot separate a genuine
crossing from a stale touch reading**, and neither reading is asserted.

## G. Persistence and replenishment — one observation

The single parent inside full-ladder coverage (Down @ 0.02, 1.99, t=1789666048):
**238.0 shares displayed at 0.02 one second before, 0 from +1 s onward, no
replenishment.** Consumed and not replaced.

That size is **`L5_COMPATIBLE`, not `L5_OWNED`** — 238 shares at his execution
price is suggestive and is not proof he placed them.

## What this case changes, and what it does not

It converts three spec fields from inference to direct observation on a named
market: **odd fragments are pieces of round parent orders**, **the 100-lot is
real and recurs**, and **simultaneous same-outcome parents one tick apart
exist**. It confirms **size does not track inventory**.

It does **not** establish the book geometry — coverage reached one parent. The
extended recorder now captures full ladders continuously, so the next complete
BTC15m window will answer F and G properly. Nothing here is a fill, a pair or a
dollar, and no order L5 did not execute is claimed to have existed.

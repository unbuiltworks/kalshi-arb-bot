# Stage B1 diagnostic — what the live evidence actually establishes

Source: `L5_LIVE_SETTLED`, `L5_WINDOW_ACCOUNTING`, `L5_LIVE_WINDOW` and the
campaign `class_budgets` snapshots, deployment `f51840b8` / commit `ff00d287`
(carries `69b19625`). Campaign commit of record: `5778eec1`.

## 1. Matched profit per contract and per active window

| basis | value |
|---|---|
| matched P&L, total | **+$0.250947** |
| matched contracts | 16.14 |
| **per matched contract** | **+$0.015548** |
| traded windows | 3 (0215z, 0230z, 0245z) |
| **per active window** | **+$0.083649** |
| armed windows | 4 |
| per armed window | +$0.062737 |

**This edge is not statistically established, and the campaign's own data says
so.** Treating each settled pair as one observation of `1.00 − pair_cost`:

| pair | qty | pair cost | edge/contract |
|---|---|---|---|
| 0215z #1 | 1.94 | 1.0000 | 0.0000 |
| 0215z #2 | 1.92 | 0.9900 | +0.0100 |
| 0215z #3 | 1.75 | 1.0100 | **−0.0100** (defensive) |
| 0230z #1 | 2.05 | 1.0000 | 0.0000 |
| 0230z #2 | 2.11 | 0.9900 | +0.0100 |
| 0230z #3 | 2.15 | 0.9900 | +0.0100 |
| 0230z #4 | 2.21 | 0.9200 | **+0.0800** |
| 0245z #1 | 2.01 | 1.0000 | 0.0000 |

mean **+$0.0125/contract**, stdev 0.0282, n=8 → **t = 1.26, one-sided
p ≈ 0.105**. Not significant at any conventional level.

Worse for the thesis: **80% of the whole matched edge comes from one pair of
eight** (0230z #4 at 0.9200). Drop it and the remaining seven average
**+$0.00286/contract**. Only **4 of 8** pairs completed strictly below $1.00;
three landed exactly at 1.0000 (zero edge) and one above it.

## 2. Residual: frequency, size, worst case, outcome

| measure | value |
|---|---|
| frequency | **1 of 3 traded windows (33%)**, 1 of 4 armed (25%) |
| size | 1.97 contracts, DOWN, 0245z |
| cost basis | $0.876947 |
| **worst case had it lost** | **−$0.876947** |
| actual outcome | DOWN settled in our favour, **+$1.093053** |
| outcome swing | $1.9700 |

The residual contributed **81% of the campaign's +$1.3440**. Its expectation at
market prices is ~0 (established in Stage B); it is variance, not edge.

## 3. Did DOWN-completion fix `69b19625` contribute to fills?

**No — not mechanically, in this campaign.** The fix is in
`src/l5_book_completion.py` (Route B). Route B placed nothing:

- 0215z: `book_completion` → `ORDER_ALREADY_RESTING` 2,
  `ECONOMICS:COMPLEMENT_DEPTH_INSUFFICIENT` 1. No placement.
- every fill came from the two-sided probe's own paired planning
  (`controller_open`: `ENTERED` 391, `NOT_REFUSED` 9 in 0215z alone), which
  plans both legs together in `probe_plan` and never needs Route B.

The 56 → 161 DOWN-completion improvement measured for `69b19625` was measured
on **823 recorded book frames**, not on live fills. The fix removed a real
latent blocker; it did not produce these pairs. Those are two separate facts
and I will not merge them.

## 4. Why 0215z and 0230z ended perfectly matched

**0230z — rounds all closed.** 8 submissions, **8 fills, 0 cancels, a 100%
fill rate**. Four pairs completed, then
`TWO_SIDED_PROBE:ACCUMULATION_EXHAUSTED` 49 — every accumulation round was
spent *productively*. `SAFETY_OVERLAY:MAX_LIVE_ORDERS` 7 shows the one-order
cap was actively serialising the work and did not prevent completion.

**0215z — no round left a first leg open.** 9 submissions, 6 fills, 3 cancels.
The 6 fills formed 3 exact pairs (`filled_total 11.22 = 2 × 5.61`); the 3
cancels were legs that never filled *and whose counterparts also never
filled*, so nothing was stranded. One stale first leg was recovered
(`acc_recovered_stale_first_leg` 1) rather than abandoned.

The common property is not luck: **in both windows every round that acquired
one leg also acquired the other before the cutoff.**

## 5. Why 0245z stranded 1.97 DOWN

Not a cap failure and not an over-fill of one complement. 0245z shows **no**
`ACCUMULATION_EXHAUSTED` and **no** `MAX_LIVE_ORDERS` — it was neither
round-limited nor order-limited. It spent **OPEN 2, COMPLETE 3, REPLACE 1**.

Two accumulation rounds ran:

- **round 0** completed: UP 2.01 + DOWN 2.01 at pair 1.0000 → matched 2.01.
- **round 1** opened a *second* first leg on the DOWN side (1.97, the cheaper
  leg, placed first by design) and **its UP counterpart never filled before
  the entry cutoff** and was cancelled.

So `up 2.01 / dn 3.98`, matched 2.01, residual 1.97 DOWN. This is the designed
behaviour of multi-round accumulation carrying its known tail risk: **the last
round's first leg can strand.** It is the single structural avoidable loss in
the campaign.

## 6. Why 0300z produced no fills

**Not the market. The campaign's own submission-class budget.** Read at
02:58:34, before window 4 armed at 03:00:20:

```
class_budgets: OPEN     {max 12, spent 12, remaining 0}   <-- EXHAUSTED
               COMPLETE {max 12, spent 10, remaining 2}
               REPLACE  {max  6, spent  1, remaining 5}
```

12 + 10 + 1 = 23 = exactly the submissions of windows 1–3. The burn:

| window | OPEN | COMPLETE | REPLACE | total |
|---|---|---|---|---|
| 0215z | 6 | 3 | 0 | 9 |
| 0230z | 4 | 4 | 0 | 8 |
| 0245z | 2 | 3 | 1 | 6 |
| **spent** | **12 / 12** | **10 / 12** | **1 / 6** | **23 / 27** |
| 0300z available | **0** | 2 | 5 | — |

0300z's decision record is consistent with exactly that:
`SUBMISSION_CLAIM:OPEN_BUDGET_EXHAUSTED` 10,
`TWO_SIDED_PROBE:ACCUMULATION_EXHAUSTED` 34.

A **second, independent** refusal also blocked opening — the completion
reserve: `submissions_per_obligation 2`, `COMPLETE remaining 2`,
`reserve_required 2` → `may_open false`,
`why_not: NO_COMPLETION_CAPACITY_RESERVED` ("refusing to open exposure the
completion budget could no longer close"). Correct behaviour.

The book was also unhelpful (0300z opened 0.42/0.52 and ran to bid 0.1400 /
ask 0.1500, UP own price below the `ts_min_first_leg_own_price` 0.20 floor),
but that is **not** why the window was silent: with OPEN at 0/12 no first leg
could have been placed at any price. Window 4 disarmed cleanly at 03:14:00
(`PAST_ENTRY_WITH_NO_INVENTORY`, residual 0) and stranded nothing.

One thing I cannot separate from these records: whether 0300z's
`ACCUMULATION_EXHAUSTED` came from rounds being claimed-then-refused or from
round retirement. The OPEN budget reading at 0/12 is sufficient on its own and
is directly observed.

## 7. Did any order or inventory constraint prevent additional profitable pairing?

**Yes, one — and it is a submission-count budget, not capital, and not the
one-live-order rule.**

| constraint | bound? | verdict |
|---|---|---|
| **OPEN submission class (12)** | **yes — fully spent, cost window 4 entirely** | the real limiter |
| completion reserve (2/obligation) | yes, in 0300z | economically correct; do not relax |
| `max_live_orders` 1 | yes (`MAX_LIVE_ORDERS` 7 in 0230z) | **serialised but did not prevent** — 0230z still filled 8/8 and matched perfectly |
| accumulation rounds (4/market) | yes in 0230z after **4 completed pairs** | bound *productively*; not a loss |
| `max_worst_case_deficit_usd` 1.50 | not reached (worst actual 0.877) | headroom |
| `max_total_exposure_usd` 20 | not reached | headroom |
| daily loss halt $5 | not reached (`room_usd` 6.344) | headroom |
| the book | 0300z only | would have refused on price anyway, but was not the operative refusal |

**What this does not say.** It does not say more orders earn more money. The
only window the OPEN budget demonstrably cost us is 0300z — and 0300z's book
was *outside the eligible price band anyway*, so the expected value of the
extra OPEN capacity on that specific window is plausibly zero. The correct
reading is narrower and it is the one that matters for design: **a 4-window
authorization was funded for roughly 3 windows of activity, so window count
and submission budget were mismatched.** That is a sizing defect in the
authorization, not evidence that more trading is more profitable.
